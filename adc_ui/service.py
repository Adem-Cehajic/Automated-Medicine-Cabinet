from __future__ import annotations

import hashlib
import hmac
import threading
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
from enum import Enum
from typing import Any, Callable, Protocol


class Role(str, Enum):
    NURSE = "Nurse"
    PHARMACIST = "Pharmacist"
    TECHNICIAN = "Technician"


class Mode(str, Enum):
    NORMAL = "Normal"
    MAINTENANCE = "Maintenance"
    FAULT = "Fault"


@dataclass(frozen=True)
class Result:
    ok: bool
    message: str
    code: str = ""
    data: Any = None


@dataclass(frozen=True)
class User:
    user_id: str
    name: str
    role: Role


@dataclass
class Medication:
    medication_id: str
    name: str
    location: str
    quantity: int
    threshold: int


@dataclass
class Alert:
    alert_id: str
    alert_type: str
    message: str
    raised_at: str
    acknowledged: bool = False


@dataclass
class UiState:
    mode: Mode
    communication_connected: bool
    current_user: User | None
    active_alerts: list[Alert] = field(default_factory=list)
    inventory: list[dict[str, Any]] = field(default_factory=list)
    unlocked_location: str | None = None
    drawer_open: bool = False
    transaction: dict[str, Any] | None = None
    faults: list[dict[str, str]] = field(default_factory=list)


class AdcService(Protocol):
    def subscribe(self, callback: Callable[[dict[str, Any]], None]) -> None: ...

    def get_state(self) -> UiState: ...

    def login(self, badge_id: str, pin: str) -> Result: ...

    def logout(self) -> Result: ...

    def touch(self) -> Result: ...

    def request_dispense(self, medication_id: str, quantity: int) -> Result: ...

    def begin_restock(self, location_id: str) -> Result: ...

    def submit_restock_quantity(self, quantity: int) -> Result: ...

    def set_threshold(self, medication_id: str, value: str) -> Result: ...

    def override_lockout(self, user_id: str) -> Result: ...

    def request_mode(self, mode: Mode) -> Result: ...

    def clear_fault(self) -> Result: ...

    def get_inventory(self) -> list[dict[str, Any]]: ...

    def get_users(self) -> list[dict[str, str]]: ...

    def get_event_log(self) -> list[dict[str, str]]: ...

    def acknowledge_alert(self, alert_id: str) -> Result: ...


class DemoAdcService:
    """In-memory service adapter for UI demos; replace with the cabinet backend."""

    def __init__(self) -> None:
        self._lock = threading.RLock()
        self._listeners: list[Callable[[dict[str, Any]], None]] = []
        self._users = {
            "N001": User("N001", "Nora Nurse", Role.NURSE),
            "P001": User("P001", "Pat Pharmacist", Role.PHARMACIST),
            "T001": User("T001", "Taylor Technician", Role.TECHNICIAN),
        }
        self._pin_hashes = {
            "N001": bytes.fromhex("67c18d94bc7a7060cff0d97e01aabb11802484fc73917e21e895c2164aab8846"),
            "P001": bytes.fromhex("e1eae8bf6b3addf74d1675b43743ea2c5e5733768e306f7c124debba87193939"),
            "T001": bytes.fromhex("2aa8f654d71d2f03161b1c253c9a1ac7de8f05ab756df126d11bd63ae72ee2a3"),
        }
        self._failed_attempts: dict[str, int] = {}
        self._locked_until: dict[str, datetime] = {}
        self._current_user: User | None = None
        self._mode = Mode.FAULT
        self._connected = True
        self._faults: list[dict[str, str]] = [
            {
                "cause": "power restored; cabinet requires inspection",
                "location": "All locations",
                "raised_at": self._now(),
            }
        ]
        self._medications = {
            "med-001": Medication("med-001", "Acetaminophen", "Drawer A1", 12, 4),
            "med-002": Medication("med-002", "Amoxicillin", "Drawer B2", 7, 3),
            "med-003": Medication("med-003", "Saline flush", "Drawer C1", 5, 5),
        }
        self._alerts: dict[str, Alert] = {}
        self._sync_fault_alert()
        self._sync_low_stock_alerts()
        self._events: list[dict[str, str]] = []
        self._transaction: dict[str, Any] | None = None
        self._drawer_open = False
        self._restock_location: str | None = None
        self._drawer_timer: threading.Timer | None = None
        self._record("system", "Startup", "Cabinet started in Fault mode")

    @staticmethod
    def _now() -> str:
        return datetime.now(timezone.utc).astimezone().strftime("%Y-%m-%d %H:%M:%S")

    @staticmethod
    def _hash_pin(pin: str, badge: str) -> bytes:
        return hashlib.pbkdf2_hmac("sha256", pin.encode("utf-8"), badge.encode("utf-8"), 120_000)

    def subscribe(self, callback: Callable[[dict[str, Any]], None]) -> None:
        self._listeners.append(callback)

    def _publish(self, event_type: str) -> None:
        event = {"type": event_type}
        for callback in tuple(self._listeners):
            callback(event)

    def _record(self, user_id: str, event_type: str, details: str) -> None:
        self._events.append(
            {"timestamp": self._now(), "event_type": event_type, "user_id": user_id, "details": details}
        )

    def get_state(self) -> UiState:
        with self._lock:
            return UiState(
                mode=self._mode,
                communication_connected=self._connected,
                current_user=self._current_user,
                active_alerts=list(self._alerts.values()),
                inventory=[
                    {
                        "medication_id": item.medication_id,
                        "name": item.name,
                        "location": item.location,
                        "quantity": item.quantity,
                        "threshold": item.threshold,
                        "low": item.quantity < item.threshold,
                    }
                    for item in self._medications.values()
                ],
                unlocked_location=(
                    self._transaction.get("location") if self._transaction else self._restock_location
                ),
                drawer_open=self._drawer_open,
                transaction=dict(self._transaction) if self._transaction else None,
                faults=[dict(fault) for fault in self._faults],
            )

    def login(self, badge_id: str, pin: str) -> Result:
        with self._lock:
            user = self._users.get(badge_id)
            if user is None:
                self._record(badge_id or "unknown", "Login denied", "Badge not recognized")
                return Result(False, "Badge not recognized", "unknown_badge")
            locked_until = self._locked_until.get(badge_id)
            if locked_until and locked_until > datetime.now(timezone.utc):
                return Result(False, "Account locked", "locked")
            expected = self._pin_hashes.get(badge_id)
            supplied = self._hash_pin(pin, badge_id)
            if expected is None or not hmac.compare_digest(expected, supplied):
                failures = self._failed_attempts.get(badge_id, 0) + 1
                self._failed_attempts[badge_id] = failures
                self._record(badge_id, "Login denied", "Incorrect PIN")
                if failures >= 3:
                    self._locked_until[badge_id] = datetime.now(timezone.utc) + timedelta(minutes=30)
                    self._record(badge_id, "Account locked", "Account locked for 30 minutes")
                    return Result(False, "Account locked for 30 minutes", "locked_out")
                return Result(False, "Incorrect PIN", "incorrect_pin")
            self._failed_attempts[badge_id] = 0
            self._locked_until.pop(badge_id, None)
            self._current_user = user
            self._record(user.user_id, "Login", "Signed in")
            self._publish("state_changed")
            return Result(True, "Signed in", "ok", user)

    def logout(self) -> Result:
        with self._lock:
            if self._current_user:
                self._record(self._current_user.user_id, "Logout", "Signed out")
            self._current_user = None
            self._publish("session_ended")
            return Result(True, "Signed out", "ok")

    def touch(self) -> Result:
        return Result(True, "Activity recorded", "ok")

    def request_dispense(self, medication_id: str, quantity: int) -> Result:
        with self._lock:
            user = self._current_user
            if user is None or user.role not in {Role.NURSE, Role.PHARMACIST}:
                return self._denied("You do not have permission to do this", "permission_denied")
            if self._mode is Mode.MAINTENANCE:
                return self._denied("Dispensing unavailable during maintenance", "wrong_mode")
            if self._mode is Mode.FAULT:
                return self._denied("Dispensing unavailable while cabinet is in Fault mode", "wrong_mode")
            medication = self._medications.get(medication_id)
            if medication is None:
                return self._denied("Medication not found", "not_found")
            if self._transaction is not None or self._restock_location is not None:
                return self._denied("Another drawer is already in use", "busy")
            if quantity < 1:
                return self._denied("Quantity must be 1 or more", "invalid_quantity")
            if quantity > medication.quantity:
                available = medication.quantity
                message = "0 units available" if available == 0 else f"Only {available} units available"
                return self._denied(message, "insufficient_stock")
            self._transaction = {
                "kind": "dispense",
                "medication_id": medication_id,
                "medication": medication.name,
                "location": medication.location,
                "quantity": quantity,
                "started_at": datetime.now(timezone.utc).timestamp(),
            }
            self._drawer_open = True
            self._record(user.user_id, "Dispense requested", f"{medication.name}: {quantity}")
            self._publish("state_changed")
            self._schedule_drawer_close()
            return Result(True, "Drawer unlocked", "unlocked", dict(self._transaction))

    def begin_restock(self, location_id: str) -> Result:
        with self._lock:
            user = self._current_user
            if user is None or user.role is not Role.PHARMACIST:
                return self._denied("You do not have permission to do this", "permission_denied")
            if self._mode is not Mode.MAINTENANCE:
                return self._denied("Restocking requires Maintenance mode", "wrong_mode")
            medication = next(
                (item for item in self._medications.values() if item.location == location_id), None
            )
            if medication is None:
                return self._denied("Location not found", "not_found")
            if self._transaction is not None or self._restock_location is not None:
                return self._denied("Another drawer is already in use", "busy")
            self._restock_location = medication.location
            self._drawer_open = True
            self._record(user.user_id, "Restock drawer unlocked", medication.location)
            self._publish("state_changed")
            return Result(True, "Drawer unlocked", "unlocked", {"location": medication.location})

    def submit_restock_quantity(self, quantity: int) -> Result:
        with self._lock:
            user = self._current_user
            if user is None or user.role is not Role.PHARMACIST:
                return self._denied("You do not have permission to do this", "permission_denied")
            if self._mode is not Mode.MAINTENANCE:
                return self._denied("Restocking requires Maintenance mode", "wrong_mode")
            if self._restock_location is None:
                return self._denied("No drawer is unlocked", "not_unlocked")
            if quantity < 1:
                return self._denied("Quantity must be 1 or more", "invalid_quantity")
            location = self._restock_location
            medication = next(item for item in self._medications.values() if item.location == location)
            medication.quantity += quantity
            self._restock_location = None
            self._drawer_open = False
            self._record(user.user_id, "Restock completed", f"{medication.name}: +{quantity}")
            self._sync_low_stock_alerts()
            self._publish("state_changed")
            return Result(
                True,
                f"Restock complete. New recorded quantity: {medication.quantity}",
                "restocked",
                {"quantity": medication.quantity, "location": location},
            )

    def set_threshold(self, medication_id: str, value: str) -> Result:
        with self._lock:
            user = self._current_user
            if user is None or user.role is not Role.PHARMACIST:
                return self._denied("You do not have permission to do this", "permission_denied")
            medication = self._medications.get(medication_id)
            if medication is None:
                return self._denied("Medication not found", "not_found")
            if not value.isdecimal():
                return self._denied("Threshold must be a whole number of 0 or more", "invalid_threshold")
            old_value = medication.threshold
            was_low = medication.quantity < medication.threshold
            medication.threshold = int(value)
            self._record(
                user.user_id,
                "Threshold changed",
                f"{medication.name}: {old_value} to {medication.threshold}",
            )
            self._sync_low_stock_alerts()
            if not was_low and medication.quantity < medication.threshold:
                self._publish("alert_raised")
            self._publish("state_changed")
            return Result(True, "Threshold saved", "ok")

    def override_lockout(self, user_id: str) -> Result:
        with self._lock:
            user = self._current_user
            if user is None or user.role is not Role.PHARMACIST:
                return self._denied("Only a pharmacist can override a lockout", "permission_denied")
            locked_until = self._locked_until.get(user_id)
            if not locked_until or locked_until <= datetime.now(timezone.utc):
                return self._denied("User is not locked out", "not_locked")
            self._locked_until.pop(user_id, None)
            self._failed_attempts[user_id] = 0
            self._record(user.user_id, "Lockout overridden", user_id)
            self._publish("state_changed")
            return Result(True, "Lockout overridden", "ok")

    def request_mode(self, mode: Mode) -> Result:
        with self._lock:
            user = self._current_user
            if user is None or user.role not in {Role.PHARMACIST, Role.TECHNICIAN}:
                return self._denied("You do not have permission to do this", "permission_denied")
            if self._mode is Mode.FAULT or mode is Mode.FAULT:
                return self._denied("Clear the fault before changing mode", "wrong_mode")
            if self._drawer_open:
                return self._denied("Close the open drawer before changing mode", "drawer_open")
            self._mode = mode
            self._record(user.user_id, "Mode changed", mode.value)
            self._publish("state_changed")
            return Result(True, f"Cabinet switched to {mode.value} mode", "ok")

    def clear_fault(self) -> Result:
        with self._lock:
            user = self._current_user
            if user is None or user.role is not Role.TECHNICIAN:
                return self._denied("You do not have permission to do this", "permission_denied")
            if self._mode is not Mode.FAULT:
                return self._denied("Cabinet is not in Fault mode", "not_fault")
            self._faults.clear()
            self._mode = Mode.MAINTENANCE
            self._sync_fault_alert()
            self._record(user.user_id, "Fault cleared", "Cabinet switched to Maintenance mode")
            self._publish("state_changed")
            return Result(True, "Fault cleared. Cabinet is now in Maintenance mode.", "ok")

    def get_inventory(self) -> list[dict[str, Any]]:
        with self._lock:
            return [
                {
                    "medication_id": item.medication_id,
                    "name": item.name,
                    "location": item.location,
                    "quantity": item.quantity,
                    "threshold": item.threshold,
                    "low": item.quantity < item.threshold,
                }
                for item in self._medications.values()
            ]

    def get_users(self) -> list[dict[str, str]]:
        with self._lock:
            return [
                {
                    "user_id": user.user_id,
                    "name": user.name,
                    "role": user.role.value,
                    "status": (
                        f"Locked out until {self._locked_until[user.user_id].astimezone().strftime('%H:%M:%S')}"
                        if self._locked_until.get(user.user_id, datetime.min.replace(tzinfo=timezone.utc))
                        > datetime.now(timezone.utc)
                        else "Active"
                    ),
                }
                for user in self._users.values()
            ]

    def get_event_log(self) -> list[dict[str, str]]:
        with self._lock:
            return list(reversed(self._events))

    def acknowledge_alert(self, alert_id: str) -> Result:
        with self._lock:
            alert = self._alerts.get(alert_id)
            if alert is None:
                return Result(False, "Alert is no longer active", "not_found")
            if alert.alert_type == "low_stock":
                alert.acknowledged = True
            return Result(True, "Alert acknowledged", "ok")

    def set_communication(self, connected: bool) -> None:
        with self._lock:
            self._connected = connected
            if connected:
                self._alerts.pop("communication", None)
            else:
                self._alerts["communication"] = Alert(
                    "communication",
                    "communication_loss",
                    "Communication with the Monitoring System has been lost. Dispensing is not affected.",
                    self._now(),
                )
            self._publish("alert_raised" if not connected else "state_changed")
            self._publish("state_changed")

    def simulate_fault(self, cause: str = "lock not responding", location: str = "Drawer A1") -> None:
        with self._lock:
            self._faults.append({"cause": cause, "location": location, "raised_at": self._now()})
            self._mode = Mode.FAULT
            self._transaction = None
            self._restock_location = None
            self._drawer_open = False
            self._cancel_drawer_timer()
            self._sync_fault_alert()
            self._record("system", "Fault raised", f"{cause} at {location}")
            self._publish("alert_raised")
            self._publish("state_changed")

    def simulate_drawer_close(self) -> None:
        with self._lock:
            if not self._drawer_open:
                return
            if self._transaction:
                transaction = self._transaction
                medication = self._medications[transaction["medication_id"]]
                was_low = medication.quantity < medication.threshold
                medication.quantity -= transaction["quantity"]
                user_id = self._current_user.user_id if self._current_user else "system"
                self._record(user_id, "Dispense completed", f"{medication.name}: {transaction['quantity']}")
                self._transaction = None
                self._sync_low_stock_alerts()
                if not was_low and medication.quantity < medication.threshold:
                    self._publish("alert_raised")
            elif self._restock_location:
                location = self._restock_location
                self._restock_location = None
                self._record("system", "Restock drawer closed", location)
            self._drawer_open = False
            self._cancel_drawer_timer()
            self._publish("location_closed")
            self._publish("state_changed")

    def _schedule_drawer_close(self) -> None:
        self._cancel_drawer_timer()
        self._drawer_timer = threading.Timer(4.0, self.simulate_drawer_close)
        self._drawer_timer.daemon = True
        self._drawer_timer.start()

    def _cancel_drawer_timer(self) -> None:
        if self._drawer_timer:
            self._drawer_timer.cancel()
            self._drawer_timer = None

    def _sync_fault_alert(self) -> None:
        if self._faults:
            fault = self._faults[-1]
            self._alerts["fault"] = Alert(
                "fault",
                "fault",
                f"Fault: {fault['cause']} at {fault['location']}.",
                fault["raised_at"],
            )
        else:
            self._alerts.pop("fault", None)

    def _sync_low_stock_alerts(self) -> None:
        for medication in self._medications.values():
            alert_id = f"low_stock:{medication.medication_id}"
            if medication.quantity < medication.threshold:
                existing = self._alerts.get(alert_id)
                self._alerts[alert_id] = Alert(
                    alert_id,
                    "low_stock",
                    f"{medication.name} is low: {medication.quantity} remaining (threshold {medication.threshold}).",
                    existing.raised_at if existing else self._now(),
                    existing.acknowledged if existing else False,
                )
            else:
                self._alerts.pop(alert_id, None)

    def _denied(self, message: str, code: str) -> Result:
        user_id = self._current_user.user_id if self._current_user else "not_logged_in"
        self._record(user_id, "Action denied", message)
        return Result(False, message, code)
