from collections.abc import Mapping
from typing import TYPE_CHECKING, Any, TypeVar, Union

from attrs import define as _attrs_define, field as _attrs_field

from ..types import UNSET, Unset

if TYPE_CHECKING:
    from ..models.boolean_in import BooleanIn
    from ..models.circuit_name_in import CircuitNameIn
    from ..models.priority_in import PriorityIn
    from ..models.relay_state_in import RelayStateIn


T = TypeVar("T", bound="BodySetCircuitStateApiV1CircuitsCircuitIdPost")


@_attrs_define
class BodySetCircuitStateApiV1CircuitsCircuitIdPost:
    """
    Attributes:
        relay_state_in (Union[Unset, RelayStateIn]):
        priority_in (Union[Unset, PriorityIn]):
        circuit_name_in (Union[Unset, CircuitNameIn]):
        user_controllable_in (Union[Unset, BooleanIn]):
        sheddable_in (Union[Unset, BooleanIn]):
        never_backup_in (Union[Unset, BooleanIn]):
    """

    relay_state_in: Union[Unset, "RelayStateIn"] = UNSET
    priority_in: Union[Unset, "PriorityIn"] = UNSET
    circuit_name_in: Union[Unset, "CircuitNameIn"] = UNSET
    user_controllable_in: Union[Unset, "BooleanIn"] = UNSET
    sheddable_in: Union[Unset, "BooleanIn"] = UNSET
    never_backup_in: Union[Unset, "BooleanIn"] = UNSET
    additional_properties: dict[str, Any] = _attrs_field(init=False, factory=dict)

    def to_dict(self) -> dict[str, Any]:
        relay_state_in: Unset | dict[str, Any] = UNSET
        if not isinstance(self.relay_state_in, Unset):
            relay_state_in = self.relay_state_in.to_dict()

        priority_in: Unset | dict[str, Any] = UNSET
        if not isinstance(self.priority_in, Unset):
            priority_in = self.priority_in.to_dict()

        circuit_name_in: Unset | dict[str, Any] = UNSET
        if not isinstance(self.circuit_name_in, Unset):
            circuit_name_in = self.circuit_name_in.to_dict()

        user_controllable_in: Unset | dict[str, Any] = UNSET
        if not isinstance(self.user_controllable_in, Unset):
            user_controllable_in = self.user_controllable_in.to_dict()

        sheddable_in: Unset | dict[str, Any] = UNSET
        if not isinstance(self.sheddable_in, Unset):
            sheddable_in = self.sheddable_in.to_dict()

        never_backup_in: Unset | dict[str, Any] = UNSET
        if not isinstance(self.never_backup_in, Unset):
            never_backup_in = self.never_backup_in.to_dict()

        field_dict: dict[str, Any] = {}
        field_dict.update(self.additional_properties)
        field_dict.update({})
        if relay_state_in is not UNSET:
            field_dict["relayStateIn"] = relay_state_in
        if priority_in is not UNSET:
            field_dict["priorityIn"] = priority_in
        if circuit_name_in is not UNSET:
            field_dict["circuitNameIn"] = circuit_name_in
        if user_controllable_in is not UNSET:
            field_dict["userControllableIn"] = user_controllable_in
        if sheddable_in is not UNSET:
            field_dict["sheddableIn"] = sheddable_in
        if never_backup_in is not UNSET:
            field_dict["neverBackupIn"] = never_backup_in

        return field_dict

    @classmethod
    def from_dict(cls: type[T], src_dict: Mapping[str, Any]) -> T:
        from ..models.boolean_in import BooleanIn
        from ..models.circuit_name_in import CircuitNameIn
        from ..models.priority_in import PriorityIn
        from ..models.relay_state_in import RelayStateIn

        d = dict(src_dict)
        _relay_state_in = d.pop("relayStateIn", UNSET)
        relay_state_in: Unset | RelayStateIn
        if isinstance(_relay_state_in, Unset):
            relay_state_in = UNSET
        else:
            relay_state_in = RelayStateIn.from_dict(_relay_state_in)

        _priority_in = d.pop("priorityIn", UNSET)
        priority_in: Unset | PriorityIn
        if isinstance(_priority_in, Unset):
            priority_in = UNSET
        else:
            priority_in = PriorityIn.from_dict(_priority_in)

        _circuit_name_in = d.pop("circuitNameIn", UNSET)
        circuit_name_in: Unset | CircuitNameIn
        if isinstance(_circuit_name_in, Unset):
            circuit_name_in = UNSET
        else:
            circuit_name_in = CircuitNameIn.from_dict(_circuit_name_in)

        _user_controllable_in = d.pop("userControllableIn", UNSET)
        user_controllable_in: Unset | BooleanIn
        if isinstance(_user_controllable_in, Unset):
            user_controllable_in = UNSET
        else:
            user_controllable_in = BooleanIn.from_dict(_user_controllable_in)

        _sheddable_in = d.pop("sheddableIn", UNSET)
        sheddable_in: Unset | BooleanIn
        if isinstance(_sheddable_in, Unset):
            sheddable_in = UNSET
        else:
            sheddable_in = BooleanIn.from_dict(_sheddable_in)

        _never_backup_in = d.pop("neverBackupIn", UNSET)
        never_backup_in: Unset | BooleanIn
        if isinstance(_never_backup_in, Unset):
            never_backup_in = UNSET
        else:
            never_backup_in = BooleanIn.from_dict(_never_backup_in)

        body_set_circuit_state_api_v1_circuits_circuit_id_post = cls(
            relay_state_in=relay_state_in,
            priority_in=priority_in,
            circuit_name_in=circuit_name_in,
            user_controllable_in=user_controllable_in,
            sheddable_in=sheddable_in,
            never_backup_in=never_backup_in,
        )

        body_set_circuit_state_api_v1_circuits_circuit_id_post.additional_properties = d
        return body_set_circuit_state_api_v1_circuits_circuit_id_post

    @property
    def additional_keys(self) -> list[str]:
        return list(self.additional_properties.keys())

    def __getitem__(self, key: str) -> Any:
        return self.additional_properties[key]

    def __setitem__(self, key: str, value: Any) -> None:
        self.additional_properties[key] = value

    def __delitem__(self, key: str) -> None:
        del self.additional_properties[key]

    def __contains__(self, key: str) -> bool:
        return key in self.additional_properties
