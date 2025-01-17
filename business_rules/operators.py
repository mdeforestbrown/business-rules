import inspect
import re
from functools import wraps
from typing import Union, Any, List, Tuple
from uuid import uuid4
import pandas
from pandas.api.types import is_integer_dtype
import sys

from .six import string_types, integer_types

from .fields import (FIELD_DATAFRAME, FIELD_TEXT, FIELD_NUMERIC, FIELD_NO_INPUT,
                     FIELD_SELECT, FIELD_SELECT_MULTIPLE)
from .utils import flatten_list, fn_name_to_pretty_label, float_to_decimal, vectorized_is_valid, vectorized_compare_dates, \
    vectorized_is_complete_date, vectorized_len, vectorized_get_dict_key, vectorized_is_in, vectorized_case_insensitive_is_in, \
    vectorized_apply_regex, apply_regex
from decimal import Decimal, Inexact, Context
import operator
import numpy as np
import pandas as pd

class BaseType(object):
    def __init__(self, value):
        self.value = self._assert_valid_value_and_cast(value)

    def _assert_valid_value_and_cast(self, value):
        raise NotImplemented()

    @classmethod
    def get_all_operators(cls):
        methods = inspect.getmembers(cls)
        return [{'name': m[0],
                 'label': m[1].label,
                 'input_type': m[1].input_type}
                for m in methods if getattr(m[1], 'is_operator', False)]

def export_type(cls):
    """ Decorator to expose the given class to business_rules.export_rule_data. """
    cls.export_in_rule_data = True
    return cls


def type_operator(input_type, label=None,
                  assert_type_for_arguments=True):
    """ Decorator to make a function into a type operator.

    - assert_type_for_arguments - if True this patches the operator function
      so that arguments passed to it will have _assert_valid_value_and_cast
      called on them to make type errors explicit.
    """
    def wrapper(func):
        func.is_operator = True
        func.label = label \
            or fn_name_to_pretty_label(func.__name__)
        func.input_type = input_type

        @wraps(func)
        def inner(self, *args, **kwargs):
            if assert_type_for_arguments:
                args = [self._assert_valid_value_and_cast(arg) for arg in args]
                kwargs = dict((k, self._assert_valid_value_and_cast(v))
                              for k, v in kwargs.items())
            return func(self, *args, **kwargs)
        return inner
    return wrapper


@export_type
class StringType(BaseType):

    name = "string"

    def _assert_valid_value_and_cast(self, value):
        value = value or ""
        if not isinstance(value, string_types):
            raise AssertionError("{0} is not a valid string type.".
                                 format(value))
        return value

    @type_operator(FIELD_TEXT)
    def equal_to(self, other_string):
        return self.value == other_string

    @type_operator(FIELD_TEXT)
    def not_equal_to(self, other_string):
        return self.value != other_string

    @type_operator(FIELD_TEXT, label="Equal To (case insensitive)")
    def equal_to_case_insensitive(self, other_string):
        return self.value.lower() == other_string.lower()

    @type_operator(FIELD_TEXT)
    def starts_with(self, other_string):
        return self.value.startswith(other_string)

    @type_operator(FIELD_TEXT)
    def ends_with(self, other_string):
        return self.value.endswith(other_string)

    @type_operator(FIELD_TEXT)
    def contains(self, other_string):
        return other_string in self.value

    @type_operator(FIELD_TEXT)
    def matches_regex(self, regex):
        return re.search(regex, self.value)

    @type_operator(FIELD_NO_INPUT)
    def non_empty(self):
        return bool(self.value)


@export_type
class NumericType(BaseType):
    EPSILON = Decimal('0.000001')

    name = "numeric"

    @staticmethod
    def _assert_valid_value_and_cast(value):
        if isinstance(value, float):
            # In python 2.6, casting float to Decimal doesn't work
            return float_to_decimal(value)
        if isinstance(value, integer_types):
            return Decimal(value)
        if isinstance(value, Decimal):
            return value
        else:
            raise AssertionError("{0} is not a valid numeric type.".
                                 format(value))

    @type_operator(FIELD_NUMERIC)
    def equal_to(self, other_numeric):
        return abs(self.value - other_numeric) <= self.EPSILON

    @type_operator(FIELD_NUMERIC)
    def not_equal_to(self, other_numeric):
        return abs(self.value - other_numeric) > self.EPSILON

    @type_operator(FIELD_NUMERIC)
    def greater_than(self, other_numeric):
        return (self.value - other_numeric) > self.EPSILON

    @type_operator(FIELD_NUMERIC)
    def greater_than_or_equal_to(self, other_numeric):
        return self.greater_than(other_numeric) or self.equal_to(other_numeric)

    @type_operator(FIELD_NUMERIC)
    def less_than(self, other_numeric):
        return (other_numeric - self.value) > self.EPSILON

    @type_operator(FIELD_NUMERIC)
    def less_than_or_equal_to(self, other_numeric):
        return self.less_than(other_numeric) or self.equal_to(other_numeric)


@export_type
class BooleanType(BaseType):

    name = "boolean"

    def _assert_valid_value_and_cast(self, value):
        if type(value) != bool:
            raise AssertionError("{0} is not a valid boolean type".
                                 format(value))
        return value

    @type_operator(FIELD_NO_INPUT)
    def is_true(self):
        return self.value

    @type_operator(FIELD_NO_INPUT)
    def is_false(self):
        return not self.value

@export_type
class SelectType(BaseType):

    name = "select"

    def _assert_valid_value_and_cast(self, value):
        if not hasattr(value, '__iter__'):
            raise AssertionError("{0} is not a valid select type".
                                 format(value))
        return value

    @staticmethod
    def _case_insensitive_equal_to(value_from_list, other_value):
        if isinstance(value_from_list, string_types) and \
                isinstance(other_value, string_types):
                    return value_from_list.lower() == other_value.lower()
        else:
            return value_from_list == other_value

    @type_operator(FIELD_SELECT, assert_type_for_arguments=False)
    def contains(self, other_value):
        for val in self.value:
            if self._case_insensitive_equal_to(val, other_value):
                return True
        return False

    @type_operator(FIELD_SELECT, assert_type_for_arguments=False)
    def does_not_contain(self, other_value):
        for val in self.value:
            if self._case_insensitive_equal_to(val, other_value):
                return False
        return True


@export_type
class SelectMultipleType(BaseType):

    name = "select_multiple"

    def _assert_valid_value_and_cast(self, value):
        if not hasattr(value, '__iter__'):
            raise AssertionError("{0} is not a valid select multiple type".
                                 format(value))
        return value

    @type_operator(FIELD_SELECT_MULTIPLE)
    def contains_all(self, other_value):
        select = SelectType(self.value)
        for other_val in other_value:
            if not select.contains(other_val):
                return False
        return True

    @type_operator(FIELD_SELECT_MULTIPLE)
    def is_contained_by(self, other_value):
        other_select_multiple = SelectMultipleType(other_value)
        return other_select_multiple.contains_all(self.value)

    @type_operator(FIELD_SELECT_MULTIPLE)
    def is_not_contained_by(self, other_value):
        return not self.is_contained_by(other_value)

    @type_operator(FIELD_SELECT_MULTIPLE)
    def shares_at_least_one_element_with(self, other_value):
        select = SelectType(self.value)
        for other_val in other_value:
            if select.contains(other_val):
                return True
        return False

    @type_operator(FIELD_SELECT_MULTIPLE)
    def shares_exactly_one_element_with(self, other_value):
        found_one = False
        select = SelectType(self.value)
        for other_val in other_value:
            if select.contains(other_val):
                if found_one:
                    return False
                found_one = True
        return found_one

    @type_operator(FIELD_SELECT_MULTIPLE)
    def shares_no_elements_with(self, other_value):
        return not self.shares_at_least_one_element_with(other_value)

@export_type
class DataframeType(BaseType):

    name = "dataframe"

    def __init__(self, data):
        self.value: pd.DataFrame = self._assert_valid_value_and_cast(data["value"])
        self.column_prefix_map = data.get("column_prefix_map", {})
        self.relationship_data = data.get("relationship_data", {})
        self.value_level_metadata = data.get("value_level_metadata", [])
        self.column_codelist_map = data.get("column_codelist_map", {})
        self.codelist_term_maps = data.get("codelist_term_maps", [])

    def _assert_valid_value_and_cast(self, value):
        if not hasattr(value, '__iter__'):
            raise AssertionError("{0} is not a valid select multiple type".
                                 format(value))
        return value

    def convert_string_data_to_lower(self, data):
        if isinstance(data, pd.core.series.Series):
            data = data.str.lower()
        else:
            data = data.lower()
        return data

    def replace_prefix(self, value: str) -> Union[str, Any]:
        if isinstance(value, str):
            for prefix, replacement in self.column_prefix_map.items():
                if value.startswith(prefix):
                    return value.replace(prefix, replacement, 1)
        return value

    def replace_all_prefixes(self, values: [str]) -> [str]:
        for i in range(len(values)):
            values[i] = self.replace_prefix(values[i])
        return values
    
    def get_comparator_data(self, comparator, value_is_literal: bool = False) -> Union[str, int, pd.Series]:
        if value_is_literal:
            return comparator
        else:
            return self.value.get(comparator, comparator)

    def is_column_of_iterables(self, column):
        return isinstance(column, pandas.core.series.Series) and (isinstance(column.iloc[0], list) or  isinstance(column.iloc[0], set))

    @type_operator(FIELD_DATAFRAME)
    def exists(self, other_value) -> pd.Series:
        target_column = self.replace_prefix(other_value.get("target"))
        return pd.Series([target_column in self.value] * len(self.value))

    @type_operator(FIELD_DATAFRAME)
    def not_exists(self, other_value):
        return ~self.exists(other_value)
    
    def _check_equality(self, row, target, comparator, value_is_literal: bool = False, case_insensitive: bool = False) -> bool:
        """
        Equality checks work slightly differently for clinical datasets. See truth table below:
        Operator       --A         --B         Outcome
        equal_to       "" or null  "" or null  False
        equal_to       "" or null  Populated   False
        equal_to       Populated   "" or null  False
        equal_to       Populated   Populated   A == B
        """
        comparison_data = comparator if comparator not in row or value_is_literal else row[comparator]
        both_null = (comparison_data == "" or comparison_data is None) & (row[target] == "" or row[target] is None)
        if both_null:
            return False
        if case_insensitive:
            target_val = row[target].lower() if row[target] else None
            comparison_val = comparison_data.lower() if comparison_data else None
            return target_val == comparison_val
        return row[target] == comparison_data

    def _check_inequality(self, row, target, comparator, value_is_literal: bool = False, case_insensitive: bool = False) -> bool:
        """
        Equality checks work slightly differently for clinical datasets. See truth table below:
        Operator       --A         --B         Outcome
        not_equal_to   "" or null  "" or null  False
        not_equal_to   "" or null  Populated   True
        not_equal_to   Populated   "" or null  True
        not_equal_to   Populated   Populated   A != B
        """
        comparison_data = comparator if comparator not in row or value_is_literal else row[comparator]
        both_null = (comparison_data == "" or comparison_data is None) & (row[target] == "" or row[target] is None)
        if both_null:
            return False
        if case_insensitive:
            target_val = row[target].lower() if row[target] else None
            comparison_val = comparison_data.lower() if comparison_data else None
            return target_val != comparison_val
        return row[target] != comparison_data

    @type_operator(FIELD_DATAFRAME)
    def equal_to(self, other_value) -> pd.Series:
        target = self.replace_prefix(other_value.get("target"))
        value_is_literal = other_value.get("value_is_literal", False)
        comparator = self.replace_prefix(other_value.get("comparator")) if not value_is_literal else other_value.get("comparator")
        return self.value.apply(lambda row: self._check_equality(row, target, comparator, value_is_literal), axis=1)

    @type_operator(FIELD_DATAFRAME)
    def equal_to_case_insensitive(self, other_value):
        target = self.replace_prefix(other_value.get("target"))
        value_is_literal = other_value.get("value_is_literal", False)
        comparator = self.replace_prefix(other_value.get("comparator")) if not value_is_literal else other_value.get("comparator")
        return self.value.apply(lambda row: self._check_equality(row, target, comparator, value_is_literal, case_insensitive=True), axis=1)

    @type_operator(FIELD_DATAFRAME)
    def not_equal_to_case_insensitive(self, other_value):
        target = self.replace_prefix(other_value.get("target"))
        value_is_literal = other_value.get("value_is_literal", False)
        comparator = self.replace_prefix(other_value.get("comparator")) if not value_is_literal else other_value.get("comparator")
        return self.value.apply(lambda row: self._check_inequality(row, target, comparator, value_is_literal, case_insensitive=True), axis=1)

    @type_operator(FIELD_DATAFRAME)
    def not_equal_to(self, other_value):
        target = self.replace_prefix(other_value.get("target"))
        value_is_literal = other_value.get("value_is_literal", False)
        comparator = self.replace_prefix(other_value.get("comparator")) if not value_is_literal else other_value.get("comparator")
        return self.value.apply(lambda row: self._check_inequality(row, target, comparator, value_is_literal), axis=1)

    @type_operator(FIELD_DATAFRAME)
    def suffix_equal_to(self, other_value: dict) -> pd.Series:
        """
        Checks if target suffix is equal to comparator.
        """
        target: str = self.replace_prefix(other_value.get("target"))
        value_is_literal: bool = other_value.get("value_is_literal", False)
        comparator: Union[str, Any] = self.replace_prefix(other_value.get("comparator")) if not value_is_literal else other_value.get("comparator")
        comparison_data: Union[str, pd.Series] = self.get_comparator_data(comparator, value_is_literal)
        suffix: int = self.replace_prefix(other_value.get("suffix"))
        return self._check_equality_of_string_part(target, comparison_data, "suffix", suffix)

    @type_operator(FIELD_DATAFRAME)
    def suffix_not_equal_to(self, other_value: dict) -> pd.Series:
        """
        Checks if target suffix is not equal to comparator.
        """
        return ~self.suffix_equal_to(other_value)

    @type_operator(FIELD_DATAFRAME)
    def prefix_equal_to(self, other_value: dict) -> pd.Series:
        """
        Checks if target prefix is equal to comparator.
        """
        target: str = self.replace_prefix(other_value.get("target"))
        value_is_literal: bool = other_value.get("value_is_literal", False)
        comparator: Union[str, Any] = self.replace_prefix(other_value.get("comparator")) if not value_is_literal else other_value.get("comparator")
        comparison_data: Union[str, pd.Series] = self.get_comparator_data(comparator, value_is_literal)
        prefix: int = self.replace_prefix(other_value.get("prefix"))
        return self._check_equality_of_string_part(target, comparison_data, "prefix", prefix)

    @type_operator(FIELD_DATAFRAME)
    def prefix_not_equal_to(self, other_value: dict) -> pd.Series:
        """
        Checks if target prefix is not equal to comparator.
        """
        return ~self.prefix_equal_to(other_value)

    @type_operator(FIELD_DATAFRAME)
    def prefix_is_contained_by(self, other_value: dict) -> pd.Series:
        """
        Checks if target prefix is contained by the comparator.
        """
        target: str = self.replace_prefix(other_value.get("target"))
        value_is_literal: bool = other_value.get("value_is_literal", False)
        comparator: Union[str, Any] = self.replace_prefix(other_value.get("comparator")) if not value_is_literal else other_value.get("comparator")
        comparison_data: Union[str, pd.Series] = self.get_comparator_data(comparator, value_is_literal)
        prefix_length: int = other_value.get("prefix")
        series_to_validate: pd.Series = self._get_string_part_series("prefix", prefix_length, target)
        return self._value_is_contained_by(series_to_validate, comparison_data)
   
    @type_operator(FIELD_DATAFRAME)
    def prefix_is_not_contained_by(self, other_value: dict) -> pd.Series:
        return ~self.prefix_is_contained_by(other_value)

    @type_operator(FIELD_DATAFRAME)
    def suffix_is_contained_by(self, other_value: dict) -> pd.Series:
        """
        Checks if target prefix is equal to comparator.
        """
        target: str = self.replace_prefix(other_value.get("target"))
        value_is_literal: bool = other_value.get("value_is_literal", False)
        comparator: Union[str, Any] = self.replace_prefix(other_value.get("comparator")) if not value_is_literal else other_value.get("comparator")
        comparison_data: Union[str, pd.Series] = self.get_comparator_data(comparator, value_is_literal)
        suffix_length: int = other_value.get("suffix")
        series_to_validate: pd.Series = self._get_string_part_series("suffix", suffix_length, target)
        return self._value_is_contained_by(series_to_validate, comparison_data)

    @type_operator(FIELD_DATAFRAME)
    def suffix_is_not_contained_by(self, other_value: dict) -> pd.Series:
        return ~self.suffix_is_contained_by(other_value)
    
    def _get_string_part_series(self, part_to_validate: str, length: int, target: str):
        if not self.value[target].apply(type).eq(str).all():
            raise ValueError("The operator can't be used with non-string values")

        if part_to_validate == "suffix":
            series_to_validate: pd.Series = self.value[target].str.slice(-length)
        elif part_to_validate == "prefix":
            series_to_validate: pd.Series = self.value[target].str.slice(stop=length)
        else:
            raise ValueError(f"Invalid part to validate: {part_to_validate}. Valid values are: suffix, prefix")

        return series_to_validate

    def _value_is_contained_by(self, series: pd.Series, comparison_data) -> pd.Series:
        if self.is_column_of_iterables(comparison_data):
            results = vectorized_is_in(series, comparison_data)
        else:
            results = series.isin(comparison_data)
        return pd.Series(results)

    def _check_equality_of_string_part(
        self,
        target: str,
        comparison_data: Union[str, pd.Series],
        part_to_validate: str,
        length: int
    ) -> pd.Series:
        """
        Checks if the given string part is equal to comparison data.
        """
        series_to_validate = self._get_string_part_series(part_to_validate, length, target)
        return series_to_validate.eq(comparison_data)

    @type_operator(FIELD_DATAFRAME)
    def less_than(self, other_value):
        target = self.replace_prefix(other_value.get("target"))
        value_is_literal = other_value.get("value_is_literal", False)
        comparator = self.replace_prefix(other_value.get("comparator")) if not value_is_literal else other_value.get("comparator")
        comparison_data = self.get_comparator_data(comparator, value_is_literal)
        target_column = pd.to_numeric(self.value[target], errors="coerce")
        if isinstance(comparison_data, pd.Series):
            comparison_data = pd.to_numeric(comparison_data, errors="coerce")
        results = np.where(target_column < comparison_data, True, False)
        return pd.Series(results)
    
    @type_operator(FIELD_DATAFRAME)
    def less_than_or_equal_to(self, other_value):
        target = self.replace_prefix(other_value.get("target"))
        value_is_literal = other_value.get("value_is_literal", False)
        comparator = self.replace_prefix(other_value.get("comparator")) if not value_is_literal else other_value.get("comparator")
        comparison_data = self.get_comparator_data(comparator, value_is_literal)
        target_column = pd.to_numeric(self.value[target], errors="coerce")
        if isinstance(comparison_data, pd.Series):
            comparison_data = pd.to_numeric(comparison_data, errors="coerce")
        results = np.where(target_column <= comparison_data, True, False)
        return pd.Series(results)
    
    @type_operator(FIELD_DATAFRAME)
    def greater_than_or_equal_to(self, other_value):
        target = self.replace_prefix(other_value.get("target"))
        value_is_literal = other_value.get("value_is_literal", False)
        comparator = self.replace_prefix(other_value.get("comparator")) if not value_is_literal else other_value.get("comparator")
        comparison_data = self.get_comparator_data(comparator, value_is_literal)
        target_column = pd.to_numeric(self.value[target], errors="coerce")
        if isinstance(comparison_data, pd.Series):
            comparison_data = pd.to_numeric(comparison_data, errors="coerce")
        results = np.where(target_column >= comparison_data, True, False)
        return pd.Series(results)
    
    @type_operator(FIELD_DATAFRAME)
    def greater_than(self, other_value):
        target = self.replace_prefix(other_value.get("target"))
        value_is_literal = other_value.get("value_is_literal", False)
        comparator = self.replace_prefix(other_value.get("comparator")) if not value_is_literal else other_value.get("comparator")
        comparison_data = self.get_comparator_data(comparator, value_is_literal)
        target_column = pd.to_numeric(self.value[target], errors="coerce")
        if isinstance(comparison_data, pd.Series):
            comparison_data = pd.to_numeric(comparison_data, errors="coerce")
        results = np.where(target_column > comparison_data, True, False)
        return pd.Series(results)

    @type_operator(FIELD_DATAFRAME)
    def contains(self, other_value):
        target = self.replace_prefix(other_value.get("target"))
        value_is_literal = other_value.get("value_is_literal", False)
        comparator = self.replace_prefix(other_value.get("comparator")) if not value_is_literal else other_value.get("comparator")
        comparison_data = self.get_comparator_data(comparator, value_is_literal)
        if self.is_column_of_iterables(self.value[target]) or isinstance(comparison_data, str):
            results = vectorized_is_in(comparison_data, self.value[target])
        elif isinstance(comparator, pandas.core.series.Series):
            results = np.where(comparison_data.isin(self.value[target]), True, False)
        else:
            # Handles numeric case. This case should never occur
            results = np.where(self.value[target] == comparison_data, True, False)
        return pd.Series(results)
    
    @type_operator(FIELD_DATAFRAME)
    def does_not_contain(self, other_value):
        return ~self.contains(other_value)

    @type_operator(FIELD_DATAFRAME)
    def contains_case_insensitive(self, other_value):
        target = self.replace_prefix(other_value.get("target"))
        value_is_literal = other_value.get("value_is_literal", False)
        comparator = self.replace_prefix(other_value.get("comparator")) if not value_is_literal else other_value.get("comparator")
        comparison_data = self.get_comparator_data(comparator, value_is_literal)
        comparison_data = self.convert_string_data_to_lower(comparison_data)
        if self.is_column_of_iterables(self.value[target]):
            results = vectorized_case_insensitive_is_in(comparison_data, self.value[target])
        elif isinstance(comparator, pandas.core.series.Series) or isinstance(comparison_data, pandas.core.series.Series):
            results = np.where(comparison_data.isin(self.value[target].str.lower()), True, False)
        else:
            results = vectorized_is_in(comparison_data.lower(), self.value[target].str.lower())
        return pd.Series(results)

    @type_operator(FIELD_DATAFRAME)
    def does_not_contain_case_insensitive(self, other_value):
        return ~self.contains_case_insensitive(other_value)

    @type_operator(FIELD_DATAFRAME)
    def is_contained_by(self, other_value):
        target = self.replace_prefix(other_value.get("target"))
        value_is_literal = other_value.get("value_is_literal", False)
        comparator = other_value.get("comparator")
        if isinstance(comparator, str) and not value_is_literal:
            # column name provided
            comparator = self.replace_prefix(comparator)
        comparison_data = self.get_comparator_data(comparator, value_is_literal)
        if self.is_column_of_iterables(comparison_data):
            results = vectorized_is_in(self.value[target], comparison_data)
        else:
            results = self.value[target].isin(comparison_data)
        return pd.Series(results)
    
    @type_operator(FIELD_DATAFRAME)
    def is_not_contained_by(self, other_value):
        return ~self.is_contained_by(other_value)
    
    @type_operator(FIELD_DATAFRAME)
    def is_contained_by_case_insensitive(self, other_value):
        target = self.replace_prefix(other_value.get("target"))
        comparator = other_value.get("comparator", [])
        value_is_literal = other_value.get("value_is_literal", False)
        if isinstance(comparator, list):
            comparator = [val.lower() for val in comparator]
        elif isinstance(comparator, str) and not value_is_literal:
            # column name provided
            comparator = self.replace_prefix(comparator)
        comparison_data = self.get_comparator_data(comparator, value_is_literal)
        if self.is_column_of_iterables(comparison_data):
            results = vectorized_case_insensitive_is_in(self.value[target].str.lower(), comparison_data)
            return pd.Series(results)
        elif isinstance(comparison_data, pd.core.series.Series):
            results = self.value[target].str.lower().isin(comparison_data.str.lower())
        else:
            results = self.value[target].str.lower().isin(comparison_data)
        return pd.Series(results.values)

    @type_operator(FIELD_DATAFRAME)
    def is_not_contained_by_case_insensitive(self, other_value):
        return ~self.is_contained_by_case_insensitive(other_value)
    
    @type_operator(FIELD_DATAFRAME)
    def prefix_matches_regex(self, other_value):
        target = self.replace_prefix(other_value.get("target"))
        comparator = other_value.get("comparator")
        prefix = other_value.get("prefix")
        results = self.value[target].map(lambda x: re.search(comparator, x[:prefix]) is not None)
        return pd.Series(results.values)
    
    @type_operator(FIELD_DATAFRAME)
    def not_prefix_matches_regex(self, other_value):
        return ~self.prefix_matches_regex(other_value)
  
    @type_operator(FIELD_DATAFRAME)
    def suffix_matches_regex(self, other_value):
        target = self.replace_prefix(other_value.get("target"))
        comparator = other_value.get("comparator")
        suffix = other_value.get("suffix")
        results = self.value[target].apply(lambda x: re.search(comparator, x[-suffix:]) is not None)
        return pd.Series(results.values)
    
    @type_operator(FIELD_DATAFRAME)
    def not_suffix_matches_regex(self, other_value):
        return ~self.suffix_matches_regex(other_value)
    
    @type_operator(FIELD_DATAFRAME)
    def matches_regex(self, other_value):
        target = self.replace_prefix(other_value.get("target"))
        comparator = other_value.get("comparator")
        results = self.value[target].str.match(comparator)
        return pd.Series(results.values)
    
    @type_operator(FIELD_DATAFRAME)
    def not_matches_regex(self, other_value):
        return ~self.matches_regex(other_value)

    @type_operator(FIELD_DATAFRAME)
    def equals_string_part(self, other_value):
        """
        Checks that the values in the target column
        equal the result of parsing the value in the comparison
        column with a regex
        """
        target = self.replace_prefix(other_value.get("target"))
        comparator = other_value.get("comparator")
        regex = other_value.get("regex")
        value_is_literal: bool = other_value.get("value_is_literal", False)
        comparison_data: Union[str, pd.Series] = self.get_comparator_data(comparator, value_is_literal)
        if isinstance(comparison_data, str):
            parsed_data = apply_regex(regex, comparison_data)
        else:
            parsed_data = pd.Series(vectorized_apply_regex(regex, comparison_data))
        print(parsed_data)
        parsed_id = str(uuid4())
        self.value[parsed_id] = parsed_data
        return self.value.apply(lambda row: self._check_equality(row, target, parsed_id, value_is_literal), axis=1)

    @type_operator(FIELD_DATAFRAME)
    def does_not_equal_string_part(self, other_value):
        return ~self.equals_string_part(other_value)

     
    @type_operator(FIELD_DATAFRAME)
    def starts_with(self, other_value):
        target = self.replace_prefix(other_value.get("target"))
        comparator = other_value.get("comparator")
        value_is_literal: bool = other_value.get("value_is_literal", False)
        comparison_data: Union[str, pd.Series] = self.get_comparator_data(comparator, value_is_literal)
        if isinstance(comparison_data, pd.Series):
            # need to convert series to tuple to make startswith operator work correctly
            comparison_data: Tuple[str] = tuple(comparison_data)
        results = self.value[target].str.startswith(comparison_data)
        return pd.Series(results.values)

    @type_operator(FIELD_DATAFRAME)
    def ends_with(self, other_value):
        target = self.replace_prefix(other_value.get("target"))
        comparator = other_value.get("comparator")
        value_is_literal: bool = other_value.get("value_is_literal", False)
        comparison_data: Union[str, pd.Series] = self.get_comparator_data(comparator, value_is_literal)
        if isinstance(comparison_data, pd.Series):
            # need to convert series to tuple to make endswith operator work correctly
            comparison_data: Tuple[str] = tuple(comparison_data)
        results = self.value[target].str.endswith(comparison_data)
        return pd.Series(results.values)

    @type_operator(FIELD_DATAFRAME)
    def has_equal_length(self, other_value: dict):
        """
        Checks that the target length is the same as comparator.
        If comparing two columns (value_is_literal is False), the operator
        compares lengths of values in these columns.
        """
        target = self.replace_prefix(other_value.get("target"))
        comparator = other_value.get("comparator")
        value_is_literal: bool = other_value.get("value_is_literal", False)
        comparison_data: Union[int, pd.Series] = self.get_comparator_data(comparator, value_is_literal)
        if isinstance(comparison_data, pd.Series):
            if is_integer_dtype(comparison_data):
                results = self.value[target].str.len().eq(comparison_data)
            else:
                results = self.value[target].str.len().eq(comparison_data.str.len())
        else:
            results = self.value[target].str.len().eq(comparator)
        return pd.Series(results)

    @type_operator(FIELD_DATAFRAME)
    def has_not_equal_length(self, other_value: dict):
        return ~self.has_equal_length(other_value)

    @type_operator(FIELD_DATAFRAME)
    def longer_than(self, other_value: dict):
        """
         Checks if the target is longer than the comparator.
         If comparing two columns (value_is_literal is False), the operator
         compares lengths of values in these columns.
         """
        target = self.replace_prefix(other_value.get("target"))
        comparator = other_value.get("comparator")
        value_is_literal: bool = other_value.get("value_is_literal", False)
        comparison_data: Union[int, pd.Series] = self.get_comparator_data(comparator, value_is_literal)
        if isinstance(comparison_data, pd.Series):
            if is_integer_dtype(comparison_data):
                results = self.value[target].str.len().gt(comparison_data)
            else:
                results = self.value[target].str.len().gt(comparison_data.str.len())
        else:
            results = self.value[target].str.len().gt(comparison_data)
        return pd.Series(results.values)

    @type_operator(FIELD_DATAFRAME)
    def longer_than_or_equal_to(self, other_value: dict):
        target = self.replace_prefix(other_value.get("target"))
        comparator = other_value.get("comparator")
        value_is_literal: bool = other_value.get("value_is_literal", False)
        comparison_data: Union[int, pd.Series] = self.get_comparator_data(comparator, value_is_literal)
        if isinstance(comparison_data, pd.Series):
            if is_integer_dtype(comparison_data):
                results = self.value[target].str.len().ge(comparison_data)
            else:
                results = self.value[target].str.len().ge(comparison_data.str.len())
        else:
            results = self.value[target].str.len().ge(comparator)
        return pd.Series(results.values)

    @type_operator(FIELD_DATAFRAME)
    def shorter_than(self, other_value: dict):
        return ~self.longer_than_or_equal_to(other_value)

    @type_operator(FIELD_DATAFRAME)
    def shorter_than_or_equal_to(self, other_value: dict):
        return ~self.longer_than(other_value)

    @type_operator(FIELD_DATAFRAME)
    def empty(self, other_value: dict):
        target = self.replace_prefix(other_value.get("target"))
        results = np.where(self.value[target].isin(["", None]), True, False)
        return pd.Series(results)

    @type_operator(FIELD_DATAFRAME)
    def empty_within_except_last_row(self, other_value: dict):
        target = self.replace_prefix(other_value.get("target"))
        comparator = other_value.get("comparator")
        # group all targets by comparator
        grouped_target = self.value.groupby(comparator)[target]
        # validate all targets except the last one
        results = grouped_target.apply(lambda x: x[:-1]).apply(lambda x: x in ["", None])
        # extract values with corresponding indexes from results
        self.value[f"result_{uuid4()}"] = results.reset_index(level=0, drop=True)
        return True in results.values

    @type_operator(FIELD_DATAFRAME)
    def non_empty(self, other_value: dict):
        return ~self.empty(other_value)

    @type_operator(FIELD_DATAFRAME)
    def non_empty_within_except_last_row(self, other_value: dict):
        target = self.replace_prefix(other_value.get("target"))
        comparator = other_value.get("comparator")
        # group all targets by comparator
        grouped_target = self.value.groupby(comparator)[target]
        # validate all targets except the last one
        results = ~grouped_target.apply(lambda x: x[:-1]).apply(lambda x: x in ["", None])
        # extract values with corresponding indexes from results
        self.value[f"result_{uuid4()}"] = results.reset_index(level=0, drop=True)
        return not(False in results.values)

    @type_operator(FIELD_DATAFRAME)
    def contains_all(self, other_value: dict):
        target = self.replace_prefix(other_value.get("target"))
        comparator = other_value.get("comparator")
        if isinstance(comparator, list):
            # get column as array of values
            values = flatten_list(self.value, comparator)
        else:
            comparator = self.replace_prefix(comparator)
            values = self.value[comparator].unique()
        return set(values).issubset(set(self.value[target].unique()))
    
    @type_operator(FIELD_DATAFRAME)
    def not_contains_all(self, other_value: dict):
        return not self.contains_all(other_value)

    @type_operator(FIELD_DATAFRAME)
    def invalid_date(self, other_value):
        target = self.replace_prefix(other_value.get("target"))
        results = ~vectorized_is_valid(self.value[target])
        return pd.Series(results)
    
    def date_comparison(self, other_value, operator):
        target = self.replace_prefix(other_value.get("target"))
        comparator = self.replace_prefix(other_value.get("comparator"))
        value_is_literal: bool = other_value.get("value_is_literal", False)
        comparison_data: Union[str, pd.Series] = self.get_comparator_data(comparator, value_is_literal)
        component = other_value.get("date_component")
        results = np.where(vectorized_compare_dates(component, self.value[target], comparison_data, operator), True, False)
        return pd.Series(results)
    
    @type_operator(FIELD_DATAFRAME)
    def date_equal_to(self, other_value):
        return self.date_comparison(other_value, operator.eq)

    @type_operator(FIELD_DATAFRAME)
    def date_not_equal_to(self, other_value):
        return self.date_comparison(other_value, operator.ne)
    
    @type_operator(FIELD_DATAFRAME)
    def date_less_than(self, other_value):
        return self.date_comparison(other_value, operator.lt)
    
    @type_operator(FIELD_DATAFRAME)
    def date_less_than_or_equal_to(self, other_value):
        return self.date_comparison(other_value, operator.le)
    
    @type_operator(FIELD_DATAFRAME)
    def date_greater_than_or_equal_to(self, other_value):
        return self.date_comparison(other_value, operator.ge)
    
    @type_operator(FIELD_DATAFRAME)
    def date_greater_than(self, other_value):
        return self.date_comparison(other_value, operator.gt)

    @type_operator(FIELD_DATAFRAME)
    def is_incomplete_date(self, other_value):
        return ~self.is_complete_date(other_value)

    @type_operator(FIELD_DATAFRAME)
    def is_complete_date(self, other_value):
        target = self.replace_prefix(other_value.get("target"))
        results = vectorized_is_complete_date(self.value[target])
        return pd.Series(results)

    @type_operator(FIELD_DATAFRAME)
    def is_unique_set(self, other_value):
        target = self.replace_prefix(other_value.get("target"))
        comparator = other_value.get("comparator")
        values = [target, comparator]
        target_data = flatten_list(self.value, values)
        target_names = []
        for target_name in target_data:
            target_name = self.replace_prefix(target_name)
            if target_name in self.value.columns:
                target_names.append(target_name)
        target_names = list(set(target_names))
        counts = self.value[target_names].groupby(target_names)[target].transform('size') 
        results = np.where(counts <= 1, True, False)
        return pd.Series(results)

    @type_operator(FIELD_DATAFRAME)
    def is_not_unique_relationship(self, other_value) -> pd.Series:
        """
        Validates one-to-one relationship between two columns (target and comparator) against a dataset.
        One-to-one means that a pair of columns can be duplicated but its integrity must not be violated:
        one value of target always corresponds to one value of comparator. Examples:

        Valid dataset:
        STUDYID  STUDYDESC
        1        A
        2        B
        3        C
        1        A
        2        B

        Invalid dataset:
        STUDYID  STUDYDESC
        1        A
        2        A
        3        C
        """
        target = self.replace_prefix(other_value.get("target"))
        comparator = other_value.get("comparator")
        if isinstance(comparator, list):
            comparator = self.replace_all_prefixes(comparator)
        else:
            comparator = self.replace_prefix(comparator)
        # remove repeating rows
        df_without_duplicates: pd.DataFrame = self.value[[target, comparator]].drop_duplicates()
        # we need to check if ANY of the columns (target or comparator) is duplicated
        duplicated_comparator: pd.Series = df_without_duplicates[comparator].duplicated(keep=False)
        duplicated_target: pd.Series = df_without_duplicates[target].duplicated(keep=False)
        result = pd.Series([False] * len(self.value))
        if duplicated_comparator.any():
            duplicated_comparator_values = set(df_without_duplicates[duplicated_comparator][comparator])
            result += self.value[comparator].isin(duplicated_comparator_values)
        if duplicated_target.any():
            duplicated_target_values = set(df_without_duplicates[duplicated_target][target])
            result += self.value[target].isin(duplicated_target_values)
        return result

    @type_operator(FIELD_DATAFRAME)
    def is_unique_relationship(self, other_value) -> pd.Series:
        return ~self.is_not_unique_relationship(other_value)

    @type_operator(FIELD_DATAFRAME)
    def is_not_unique_set(self, other_value):
        return ~self.is_unique_set(other_value)
        
    @type_operator(FIELD_DATAFRAME)
    def is_ordered_set(self, other_value):
        target = self.replace_prefix(other_value.get("target"))
        value = other_value.get("comparator")
        if not isinstance(value, str):
            raise Exception('Comparator must be a single String value')
            
        return not (False in self.value.groupby(value).agg(lambda x : list(x))[target].map(lambda x: sorted(x) == x).tolist())
       
    @type_operator(FIELD_DATAFRAME)
    def is_not_ordered_set(self, other_value):
        target = self.replace_prefix(other_value.get("target"))
        value = other_value.get("comparator")
        if not isinstance(value, str):
            raise Exception('Comparator must be a single String value')
            
        return False in self.value.groupby(value).agg(lambda x : list(x))[target].map(lambda x: sorted(x) == x).tolist() 

    @type_operator(FIELD_DATAFRAME)
    def is_valid_reference(self, other_value):
        target = self.replace_prefix(other_value.get("target"))
        context = self.replace_prefix(other_value.get("context"))
        if context:
            results = self.value.apply(lambda row: row[target] in self.relationship_data.get(row[context], {}), axis=1)
        else:
            results = self.value[target].isin(self.relationship_data)
        return pd.Series(results.values)

    @type_operator(FIELD_DATAFRAME)
    def is_not_valid_reference(self, other_value):
        return ~self.is_valid_reference(other_value)

    @type_operator(FIELD_DATAFRAME)
    def is_valid_relationship(self, other_value):
        target = self.replace_prefix(other_value.get("target"))
        value_column = self.replace_prefix(other_value.get("comparator"))
        context = self.replace_prefix(other_value.get("context"))
        results = self.value.apply(lambda row: self.detect_reference(row, value_column, target, context), axis=1)
        return pd.Series(results.values)

    @type_operator(FIELD_DATAFRAME)
    def is_not_valid_relationship(self, other_value):
        return ~self.is_valid_relationship(other_value)

    @type_operator(FIELD_DATAFRAME)
    def non_conformant_value_data_type(self, other_value):
        results = False
        for vlm in self.value_level_metadata:
            results |= self.value.apply(lambda row: vlm["filter"](row) and not vlm["type_check"](row), axis=1)
        return pd.Series(results.values)

    @type_operator(FIELD_DATAFRAME)
    def non_conformant_value_length(self, other_value):
        results = False
        for vlm in self.value_level_metadata:
            results |= self.value.apply(lambda row: vlm["filter"](row) and not vlm["length_check"](row), axis=1)
        return pd.Series(results.values)
    
    @type_operator(FIELD_DATAFRAME)
    def conformant_value_data_type(self, other_value):
        results = False
        for vlm in self.value_level_metadata:
            results |= self.value.apply(lambda row: vlm["filter"](row) and vlm["type_check"](row), axis=1)
        return pd.Series(results.values)

    @type_operator(FIELD_DATAFRAME)
    def conformant_value_length(self, other_value):
        results = False
        for vlm in self.value_level_metadata:
            results |= self.value.apply(lambda row: vlm["filter"](row) and vlm["length_check"](row), axis=1)
        return pd.Series(results.values)

    @type_operator(FIELD_DATAFRAME)
    def has_next_corresponding_record(self, other_value: dict):
        """
        The operator ensures that value of target in current row
        is the same as value of comparator in the next row.
        In order to achieve this, we just remove last row from target
        and first row from comparator and compare the resulting contents.
        The result is reported for target.
        """
        target = self.replace_prefix(other_value.get("target"))
        comparator = self.replace_prefix(other_value.get("comparator"))
        group_by_column: str = self.replace_prefix(other_value.get("within"))
        order_by_column: str = self.replace_prefix(other_value.get("ordering"))
        ordered_df = self.value.sort_values(by=[order_by_column])
        grouped_df = ordered_df.groupby(group_by_column)
        results = grouped_df.apply(lambda x: self.compare_target_with_comparator_next_row(x, target, comparator))
        return pd.Series(results.explode().tolist())

    @type_operator(FIELD_DATAFRAME)
    def does_not_have_next_corresponding_record(self, other_value: dict):
        return ~self.has_next_corresponding_record(other_value)

    def compare_target_with_comparator_next_row(self, df: pd.DataFrame, target: str, comparator: str):
        """
        Compares current row of a target with the next row of comparator.
        We can't compare last row of target with the next row of comparator
        because there is no row after the last one.
        """
        target_without_last_row = df[target].drop(df[target].tail(1).index)
        comparator_without_first_row = df[comparator].drop(df[comparator].head(1).index)
        results = np.where(target_without_last_row.values == comparator_without_first_row.values, True, False)
        return [*results, pandas.NA]  # appending NA here to make the length of results list the same as length of df

    @type_operator(FIELD_DATAFRAME)
    def present_on_multiple_rows_within(self, other_value: dict):
        """
        The operator ensures that the target is present on multiple rows
        within a group_by column. The dataframe is grouped by a certain column
        and the check is applied to each group.
        """
        target = self.replace_prefix(other_value.get("target"))
        min_count: int = other_value.get("comparator") or 1
        group_by_column = self.replace_prefix(other_value.get("within"))
        grouped = self.value.groupby(group_by_column)
        results = grouped.apply(lambda x: self.validate_series_length(x[target], min_count))
        return pd.Series(results.explode().tolist())

    def validate_series_length(self, ser: pd.Series, min_length: int):
        if len(ser) > min_length:
            return [True] * len(ser)
        else:
            return [False] * min_length

    @type_operator(FIELD_DATAFRAME)
    def not_present_on_multiple_rows_within(self, other_value: dict):
        return ~self.present_on_multiple_rows_within(other_value)

    def detect_reference(self, row, value_column, target_column, context=None):
        if context:
            target_data = self.relationship_data.get(row[context], {}).get(row[target_column], pd.Series([]).values)
        else:
            target_data = self.relationship_data.get(row[target_column], pd.Series([]).values)
        value = row[value_column]
        return (value in target_data) or (value in target_data.astype(int).astype(str)) or (value in target_data.astype(str))

    @type_operator(FIELD_DATAFRAME)
    def additional_columns_empty(self, other_value: dict):
        """
        The dataframe column might have some additional columns.
        If the next additional column exists, the previous one cannot be empty.
        Example:
            column - TSVAL
            additional columns - TSVAL1, TSVAL2, ...
            If TSVAL2 exists -> TSVAL1 cannot be empty.
            Original column (TSVAL) can be empty.

        The operator extracts these additional columns from the DF
        and ensures they are not empty.
        """
        target: str = self.replace_prefix(other_value.get("target"))
        regex: str = rf"^{target}\d+$"  # starting from target, ending with integers and nothing is between them
        df: pd.DataFrame = self.value.filter(regex=regex)
        # applying a function to each row
        result: pd.Series = df.apply(lambda row: self.next_column_exists_and_previous_is_null(row), axis=1)
        return result

    @type_operator(FIELD_DATAFRAME)
    def additional_columns_not_empty(self, other_value: dict):
        return ~self.additional_columns_empty(other_value)

    @type_operator(FIELD_DATAFRAME)
    def references_correct_codelist(self, other_value: dict):
        target: str = self.replace_prefix(other_value.get("target"))
        comparator = self.replace_prefix(other_value.get("comparator"))
        result: pd.Series = self.value.apply(lambda row: self.valid_codelist_reference(row[target], row[comparator]), axis=1)
        return result
    
    @type_operator(FIELD_DATAFRAME)
    def does_not_reference_correct_codelist(self, other_value: dict):
        return ~self.references_correct_codelist(other_value)

    @type_operator(FIELD_DATAFRAME)
    def uses_valid_codelist_terms(self, other_value: dict):
        target: str = self.replace_prefix(other_value.get("target"))
        comparator = self.replace_prefix(other_value.get("comparator"))
        result: pd.Series = self.value.apply(lambda row: self.valid_terms(row[target], row[comparator]), axis=1)
        return result

    @type_operator(FIELD_DATAFRAME)
    def does_not_use_valid_codelist_terms(self, other_value: dict):
        return ~self.uses_valid_codelist_terms(other_value)

    def next_column_exists_and_previous_is_null(self, row: pd.Series) -> bool:
        row.reset_index(drop=True, inplace=True)
        for index in row[row.isin([[], {}, "", None])].index:  # leaving null values only
            next_position: int = index + 1
            if next_position < len(row) and row[next_position] is not None:
                return True
        return False
    
    def valid_codelist_reference(self, column_name, codelist):
        if column_name in self.column_codelist_map:
            return codelist in self.column_codelist_map[column_name]
        elif self.column_prefix_map:
            # Check for generic versions of variables (i.e --DECOD)
            for key in self.column_prefix_map:
                if column_name.startswith(self.column_prefix_map[key]):
                    generic_column_name = column_name.replace(self.column_prefix_map[key], key, 1)
                    if generic_column_name in self.column_codelist_map:
                        return codelist in self.column_codelist_map.get(generic_column_name)
        return True
    
    def valid_terms(self, codelist, terms_list):
        if not codelist:
            return True
        valid_term = False
        for codelist_term_map in self.codelist_term_maps:
            if codelist in codelist_term_map:
                valid_term = valid_term or (codelist_term_map[codelist].get("extensible") or set(terms_list).issubset(codelist_term_map[codelist].get("allowed_terms", [])))
        return valid_term

    @type_operator(FIELD_DATAFRAME)
    def has_different_values(self, other_value: dict):
        """
        The operator ensures that the target column has different values.
        """
        target: str = self.replace_prefix(other_value.get("target"))
        is_valid: bool = len(self.value[target].unique()) > 1
        return pandas.Series([is_valid] * len(self.value[target]))

    @type_operator(FIELD_DATAFRAME)
    def has_same_values(self, other_value: dict):
        return ~self.has_different_values(other_value)

    @type_operator(FIELD_DATAFRAME)
    def is_ordered_by(self, other_value: dict) -> pd.Series:
        """
        Checking validity based on target order.
        """
        target: str = self.replace_prefix(other_value.get("target"))
        sort_order: str =other_value.get("order","asc")
        if sort_order not in ["asc","dsc"]:
            raise ValueError ("invalid sorting order")
        sort_order_bool: bool = sort_order == "asc"
        return self.value[target].eq(self.value[target].sort_values(ascending=sort_order_bool, ignore_index=True))

    @type_operator(FIELD_DATAFRAME)
    def is_not_ordered_by(self, other_value: dict) -> pd.Series:
        return ~self.is_ordered_by(other_value)

    @type_operator(FIELD_DATAFRAME)
    def value_has_multiple_references(self, other_value: dict) -> pd.Series:
        """
        Requires a target column and a reference count column whose values
        are a dictionary containing the number of times that value appears.
        """
        target: str = self.replace_prefix(other_value.get("target"))
        reference_count_column: str = self.replace_prefix(other_value.get("comparator"))
        result = np.where(vectorized_get_dict_key(self.value[reference_count_column], self.value[target]) > 1, True, False)
        return pd.Series(result)

    @type_operator(FIELD_DATAFRAME)
    def value_does_not_have_multiple_references(self, other_value: dict) -> pd.Series:
        return ~self.value_has_multiple_references(other_value)

    @type_operator(FIELD_DATAFRAME)
    def target_is_sorted_by(self, other_value: dict) -> pd.Series:
        """
         Checking the sort order based on  comparators
        """
        target: str = self.replace_prefix(other_value.get("target"))
        within: str = self.replace_prefix(other_value.get("within"))
        columns = other_value["comparator"]
        for col in columns:
            comparator: str = self.replace_prefix(col["name"])
            ascending: str = col["sort_order"] != "DESC"
            na_pos: str = col["null_position"]

            grouped_df: pd.Series = self.value.sort_values(
                by=[within, comparator],
                na_position=na_pos
            ).groupby([within]).apply(lambda x: x)
            temp_target: pd.Series = grouped_df.groupby(within).cumcount().apply(lambda x: x+1)
            if not ascending:
                grouped_df = grouped_df.reset_index(drop=True)
                temp_target = temp_target[::-1].reset_index(drop=True)
        return temp_target.eq(grouped_df[target]).sort_index(axis=0)

    @type_operator(FIELD_DATAFRAME)
    def target_is_not_sorted_by(self, other_value: dict) -> pd.Series:
        return ~self.target_is_sorted_by(other_value)

    @type_operator(FIELD_DATAFRAME)
    def variable_metadata_equal_to(self, other_value: dict) -> pd.Series:
        """
        Validates the metadata for variables, provided in the metadata column, is equal to
        the comparator.
        Ex.
        target: STUDYID
        comparator: "Exp"
        metadata_column: {"STUDYID": "Req", "DOMAIN": "Req"}
        result: False       
        """
        target = self.replace_prefix(other_value.get("target"))
        comparator = other_value.get("comparator") # Assumes the comparator is a value not a column
        metadata_column = self.replace_prefix(other_value.get("metadata"))
        result = np.where(vectorized_get_dict_key(self.value[metadata_column], target) == comparator, True, False)
        return pd.Series(result)
    
    @type_operator(FIELD_DATAFRAME)
    def variable_metadata_not_equal_to(self, other_value: dict) -> pd.Series:
        return ~self.variable_metadata_equal_to(other_value)

@export_type
class GenericType(SelectMultipleType, SelectType, StringType, NumericType, BooleanType):

    """
    This is meant to be a generic operator type to support all operations on a given value. Use this when you don't know the type of the value that will be returned.
    """
    EPSILON = Decimal('0.000001')
    name = "generic"

    def _assert_valid_value_and_cast(self, value):        
        if isinstance(value, string_types):
            # String type
            return str(value)
        
        elif isinstance(value, float):
            # In python 2.6, casting float to Decimal doesn't work
            return float_to_decimal(value)
        elif isinstance(value, integer_types):
            return Decimal(value)
        else:
            return value

    def equal_to(self, other):
        if isinstance(self.value, Decimal):
            return self.num_equal_to(other)
        else:
            return self.str_equal_to(other)
    
    def not_equal_to(self, other):
        if isinstance(self.value, Decimal):
            return self.num_not_equal_to(other)
        else:
            return self.str_not_equal_to(other)

    def is_contained_by(self, other_value):
        if not isinstance(self.value, list):
            self.value = [self.value]
        return super().is_contained_by(other_value)

    @type_operator(FIELD_NUMERIC)
    def num_equal_to(self, other_numeric):
        return abs(self.value - other_numeric) <= self.EPSILON
    
    @type_operator(FIELD_TEXT)
    def str_equal_to(self, other_string):
        return self.value == other_string

    @type_operator(FIELD_NUMERIC)
    def num_not_equal_to(self, other_numeric):
        return abs(self.value - other_numeric) > self.EPSILON

    @type_operator(FIELD_TEXT)
    def str_not_equal_to(self, other_string):
        return self.value != other_string

    @type_operator(FIELD_TEXT)
    def contains(self, other_string):
        return other_string in self.value
