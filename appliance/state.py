"""Transient appliance state machine; durable operator intent lives elsewhere."""

from __future__ import annotations

from enum import Enum
from typing import Optional, Set


class State(str, Enum):
    SAFE_OFF = "SAFE_OFF"
    READY = "READY"
    ON_AIR = "ON_AIR"
    FAULT = "FAULT"


# Allowed transitions
_TRANSITIONS = {
    State.SAFE_OFF: {State.READY, State.FAULT, State.SAFE_OFF},
    State.READY: {State.SAFE_OFF, State.ON_AIR, State.FAULT, State.READY},
    State.ON_AIR: {State.SAFE_OFF, State.FAULT, State.READY},
    State.FAULT: {State.SAFE_OFF, State.FAULT},
}  # type: dict


class StateError(RuntimeError):
    pass


class StateMachine:
    def __init__(self) -> None:
        self._state = State.SAFE_OFF
        self.fault_reason = None  # type: Optional[str]

    @property
    def state(self) -> State:
        return self._state

    def can(self, target: State) -> bool:
        return target in _TRANSITIONS[self._state]

    def transition(self, target: State, reason: str = "") -> State:
        if target not in _TRANSITIONS[self._state]:
            raise StateError(
                "illegal transition {} -> {} ({})".format(
                    self._state.value, target.value, reason
                )
            )
        self._state = target
        if target == State.FAULT:
            self.fault_reason = reason or "fault"
        elif target != State.FAULT:
            if target == State.SAFE_OFF:
                self.fault_reason = None
        return self._state

    def enter_fault(self, reason: str) -> State:
        # FAULT may be entered from any state except we allow from all via force
        self._state = State.FAULT
        self.fault_reason = reason
        return self._state

    def reset_to_safe(self) -> State:
        self._state = State.SAFE_OFF
        self.fault_reason = None
        return self._state
