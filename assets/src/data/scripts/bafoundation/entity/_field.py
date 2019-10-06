# Synced from bamaster.
# EFRO_SYNC_HASH=1181984339043224435868827486253284940
#
"""Field types for the entity system."""

from __future__ import annotations

import copy
import logging
from typing import TYPE_CHECKING, Generic, TypeVar, overload

from bafoundation.entity._support import (BaseField, BoundCompoundValue,
                                          BoundListField, BoundDictField,
                                          BoundCompoundListField,
                                          BoundCompoundDictField)

if TYPE_CHECKING:
    from typing import Dict, Type, List, Any
    from bafoundation.entity._value import TypedValue, CompoundValue
    from bafoundation.entity._support import FieldInspector

T = TypeVar('T')
TK = TypeVar('TK')
TC = TypeVar('TC', bound='CompoundValue')


class Field(BaseField, Generic[T]):
    """Field consisting of a single value."""

    def __init__(self,
                 d_key: str,
                 value: 'TypedValue[T]',
                 store_default: bool = False) -> None:
        super().__init__(d_key)
        self.d_value = value
        self._store_default = store_default

    def __repr__(self) -> str:
        return f'<Field "{self.d_key}" with {self.d_value}>'

    def get_default_data(self) -> Any:
        return self.d_value.get_default_data()

    def filter_input(self, data: Any, error: bool) -> Any:
        return self.d_value.filter_input(data, error)

    def filter_output(self, data: Any) -> Any:
        return self.d_value.filter_output(data)

    def prune_data(self, data: Any) -> bool:
        return self.d_value.prune_data(data)

    if TYPE_CHECKING:
        # Use default runtime get/set but let type-checker know our types.
        # Note: we actually return a bound-field when accessed on
        # a type instead of an instance, but we don't reflect that here yet
        # (need to write a mypy plugin so sub-field access works first)

        def __get__(self, obj: Any, cls: Any = None) -> T:
            ...

        def __set__(self, obj: Any, value: T) -> None:
            ...


class CompoundField(BaseField, Generic[TC]):
    """Field consisting of a single compound value."""

    def __init__(self, d_key: str, value: TC,
                 store_default: bool = False) -> None:
        super().__init__(d_key)
        if __debug__ is True:
            from bafoundation.entity._value import CompoundValue
            assert isinstance(value, CompoundValue)
            assert not hasattr(value, 'd_data')
        self.d_value = value
        self._store_default = store_default

    def get_default_data(self) -> dict:
        return self.d_value.get_default_data()

    def filter_input(self, data: Any, error: bool) -> dict:
        return self.d_value.filter_input(data, error)

    def prune_data(self, data: Any) -> bool:
        return self.d_value.prune_data(data)

    # Note:
    # Currently, to the type-checker we just return a simple instance
    # of our CompoundValue so it can properly type-check access to its
    # attrs. However at runtime we return a FieldInspector or
    # BoundCompoundField which both use magic to provide the same attrs
    # dynamically (but which the type-checker doesn't understand).
    # Perhaps at some point we can write a mypy plugin to correct this.
    if TYPE_CHECKING:

        def __get__(self, obj: Any, cls: Any = None) -> TC:
            ...

        # Theoretically this type-checking may be too tight;
        # we can support assigning a parent class to a child class if
        # their fields match.  Not sure if that'll ever come up though;
        # gonna leave this for now as I prefer to have *some* checking.
        # Also once we get BoundCompoundValues working with mypy we'll
        # need to accept those too.
        def __set__(self: CompoundField[TC], obj: Any, value: TC) -> None:
            ...

    else:

        def __get__(self, obj, cls=None):
            if obj is None:
                # when called on the type, we return the field
                return self
            # (this is only ever called on entity root fields
            # so no need to worry about custom d_key case)
            assert self.d_key in obj.d_data
            return BoundCompoundValue(self.d_value, obj.d_data[self.d_key])

        def __set__(self, obj, value):
            from bafoundation.entity._value import CompoundValue

            # Ok here's the deal: our type checking above allows any subtype
            # of our CompoundValue in here, but we want to be more picky than
            # that. Let's check fields for equality. This way we'll allow
            # assigning something like a Carentity to a Car field
            # (where the data is the same), but won't allow assigning a Car
            # to a Vehicle field (as Car probably adds more fields).
            value1: CompoundValue
            if isinstance(value, BoundCompoundValue):
                value1 = value.d_value
            elif isinstance(value, CompoundValue):
                value1 = value
            else:
                raise ValueError(
                    f"Can't assign from object type {type(value)}")
            data = getattr(value, 'd_data', None)
            if data is None:
                raise ValueError(f"Can't assign from unbound object {value}")
            if self.d_value.get_fields() != value1.get_fields():
                raise ValueError(f"Can't assign to {self.d_value} from"
                                 f" incompatible type {value.d_value}; "
                                 f"sub-fields do not match.")

            # If we're allowing this to go through, we can simply copy the
            # data from the passed in value. The fields match so it should
            # be in a valid state already.
            obj.d_data[self.d_key] = copy.deepcopy(data)


class ListField(BaseField, Generic[T]):
    """Field consisting of repeated values."""

    def __init__(self,
                 d_key: str,
                 value: 'TypedValue[T]',
                 store_default: bool = False) -> None:
        super().__init__(d_key)
        self.d_value = value
        self._store_default = store_default

    def get_default_data(self) -> list:
        return []

    def filter_input(self, data: Any, error: bool) -> Any:
        if not isinstance(data, list):
            if error:
                raise TypeError('list value expected')
            logging.error('Ignoring non-list data for %s: %s', self, data)
            data = []
        for i, entry in enumerate(data):
            data[i] = self.d_value.filter_input(entry, error=error)
        return data

    def prune_data(self, data: Any) -> bool:
        # We never prune individual values since that would fundamentally
        # change the list, but we can prune completely if empty (and allowed).
        return not data and not self._store_default

    # When accessed on a FieldInspector we return a sub-field FieldInspector.
    # When accessed on an instance we return a BoundListField.

    @overload
    def __get__(self, obj: None, cls: Any = None) -> FieldInspector:
        ...

    @overload
    def __get__(self, obj: Any, cls: Any = None) -> BoundListField[T]:
        ...

    def __get__(self, obj: Any, cls: Any = None) -> Any:
        if obj is None:
            # When called on the type, we return the field.
            return self
        return BoundListField(self, obj.d_data[self.d_key])

    if TYPE_CHECKING:

        def __set__(self, obj: Any, value: List[T]) -> None:
            ...


class DictField(BaseField, Generic[TK, T]):
    """A field of values in a dict with a specified index type."""

    def __init__(self,
                 d_key: str,
                 keytype: Type[TK],
                 field: 'TypedValue[T]',
                 store_default: bool = False) -> None:
        super().__init__(d_key)
        self.d_value = field
        self._store_default = store_default
        self._keytype = keytype

    def get_default_data(self) -> dict:
        return {}

    # noinspection DuplicatedCode
    def filter_input(self, data: Any, error: bool) -> Any:
        if not isinstance(data, dict):
            if error:
                raise TypeError('dict value expected')
            logging.error('Ignoring non-dict data for %s: %s', self, data)
            data = {}
        data_out = {}
        for key, val in data.items():
            if not isinstance(key, self._keytype):
                if error:
                    raise TypeError('invalid key type')
                logging.error('Ignoring invalid key type for %s: %s', self,
                              data)
                continue
            data_out[key] = self.d_value.filter_input(val, error=error)
        return data_out

    def prune_data(self, data: Any) -> bool:
        # We never prune individual values since that would fundamentally
        # change the dict, but we can prune completely if empty (and allowed)
        return not data and not self._store_default

    @overload
    def __get__(self, obj: None, cls: Any = None) -> DictField[TK, T]:
        ...

    @overload
    def __get__(self, obj: Any, cls: Any = None) -> BoundDictField[TK, T]:
        ...

    def __get__(self, obj: Any, cls: Any = None) -> Any:
        if obj is None:
            # When called on the type, we return the field.
            return self
        return BoundDictField(self._keytype, self, obj.d_data[self.d_key])

    if TYPE_CHECKING:

        def __set__(self, obj: Any, value: Dict[TK, T]) -> None:
            ...


class CompoundListField(BaseField, Generic[TC]):
    """A field consisting of repeated instances of a compound-value.

    Element access returns the sub-field, allowing nested field access.
    ie: mylist[10].fieldattr = 'foo'
    """

    def __init__(self, d_key: str, valuetype: TC,
                 store_default: bool = False) -> None:
        super().__init__(d_key)
        self.d_value = valuetype

        # This doesnt actually exist for us, but want the type-checker
        # to think it does (see TYPE_CHECKING note below).
        self.d_data: Any
        self._store_default = store_default

    def filter_input(self, data: Any, error: bool) -> list:
        if not isinstance(data, list):
            if error:
                raise TypeError('list value expected')
            logging.error('Ignoring non-list data for %s: %s', self, data)
            data = []
        assert isinstance(data, list)

        # Ok we've got a list; now run everything in it through validation.
        for i, subdata in enumerate(data):
            data[i] = self.d_value.filter_input(subdata, error=error)
        return data

    def get_default_data(self) -> list:
        return []

    def prune_data(self, data: Any) -> bool:
        # Run pruning on all individual entries' data through out child field.
        # However we don't *completely* prune values from the list since that
        # would change it.
        for subdata in data:
            self.d_value.prune_fields_data(subdata)

        # We can also optionally prune the whole list if empty and allowed.
        return not data and not self._store_default

    @overload
    def __get__(self, obj: None, cls: Any = None) -> CompoundListField[TC]:
        ...

    @overload
    def __get__(self, obj: Any, cls: Any = None) -> BoundCompoundListField[TC]:
        ...

    def __get__(self, obj: Any, cls: Any = None) -> Any:
        # On access we simply provide a version of ourself
        # bound to our corresponding sub-data.
        if obj is None:
            # when called on the type, we return the field
            return self
        assert self.d_key in obj.d_data
        return BoundCompoundListField(self, obj.d_data[self.d_key])

    # Note:
    # When setting the list, we tell the type-checker that we accept
    # a raw list of CompoundValue objects, but at runtime we actually
    # deal with BoundCompoundValue objects (see note in BoundCompoundListField)
    if TYPE_CHECKING:

        def __set__(self, obj: Any, value: List[TC]) -> None:
            ...

    else:

        def __set__(self, obj, value):
            if not isinstance(value, list):
                raise TypeError(
                    'CompoundListField expected list value on set.')

            # Allow assigning only from a sequence of our existing children.
            # (could look into expanding this to other children if we can
            # be sure the underlying data will line up; for example two
            # CompoundListFields with different child_field values should not
            # be inter-assignable.
            if (not all(isinstance(i, BoundCompoundValue) for i in value)
                    or not all(i.d_value is self.d_value for i in value)):
                raise ValueError('CompoundListField assignment must be a '
                                 'list containing only its existing children.')
            obj.d_data[self.d_key] = [i.d_data for i in value]


class CompoundDictField(BaseField, Generic[TK, TC]):
    """A field consisting of key-indexed instances of a compound-value.

    Element access returns the sub-field, allowing nested field access.
    ie: mylist[10].fieldattr = 'foo'
    """

    def __init__(self,
                 d_key: str,
                 keytype: Type[TK],
                 valuetype: TC,
                 store_default: bool = False) -> None:
        super().__init__(d_key)
        self.d_value = valuetype

        # This doesnt actually exist for us, but want the type-checker
        # to think it does (see TYPE_CHECKING note below).
        self.d_data: Any
        self.d_keytype = keytype
        self._store_default = store_default

    # noinspection DuplicatedCode
    def filter_input(self, data: Any, error: bool) -> dict:
        if not isinstance(data, dict):
            if error:
                raise TypeError('dict value expected')
            logging.error('Ignoring non-dict data for %s: %s', self, data)
            data = {}
        data_out = {}
        for key, val in data.items():
            if not isinstance(key, self.d_keytype):
                if error:
                    raise TypeError('invalid key type')
                logging.error('Ignoring invalid key type for %s: %s', self,
                              data)
                continue
            data_out[key] = self.d_value.filter_input(val, error=error)
        return data_out

    def get_default_data(self) -> dict:
        return {}

    def prune_data(self, data: Any) -> bool:
        # Run pruning on all individual entries' data through our child field.
        # However we don't *completely* prune values from the list since that
        # would change it.
        for subdata in data.values():
            self.d_value.prune_fields_data(subdata)

        # We can also optionally prune the whole list if empty and allowed.
        return not data and not self._store_default

    @overload
    def __get__(self, obj: None, cls: Any = None) -> CompoundDictField[TK, TC]:
        ...

    @overload
    def __get__(self, obj: Any,
                cls: Any = None) -> BoundCompoundDictField[TK, TC]:
        ...

    def __get__(self, obj: Any, cls: Any = None) -> Any:
        # On access we simply provide a version of ourself
        # bound to our corresponding sub-data.
        if obj is None:
            # when called on the type, we return the field
            return self
        assert self.d_key in obj.d_data
        return BoundCompoundDictField(self, obj.d_data[self.d_key])

    # In the type-checker's eyes we take CompoundValues but at runtime
    # we actually take BoundCompoundValues (see note in BoundCompoundDictField)
    if TYPE_CHECKING:

        def __set__(self, obj: Any, value: Dict[TK, TC]) -> None:
            ...

    else:

        def __set__(self, obj, value):
            if not isinstance(value, dict):
                raise TypeError(
                    'CompoundDictField expected dict value on set.')

            # Allow assigning only from a sequence of our existing children.
            # (could look into expanding this to other children if we can
            # be sure the underlying data will line up; for example two
            # CompoundListFields with different child_field values should not
            # be inter-assignable.
            print('val', value)
            if (not all(isinstance(i, self.d_keytype) for i in value.keys())
                    or not all(
                        isinstance(i, BoundCompoundValue)
                        for i in value.values())
                    or not all(i.d_value is self.d_value
                               for i in value.values())):
                raise ValueError('CompoundDictField assignment must be a '
                                 'dict containing only its existing children.')
            obj.d_data[self.d_key] = {
                key: val.d_data
                for key, val in value.items()
            }