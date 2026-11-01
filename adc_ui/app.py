from __future__ import annotations

import logging
import queue
import sys
import threading
import tkinter as tk
from concurrent.futures import Future, ThreadPoolExecutor
from datetime import datetime
from tkinter import ttk
from typing import Any, Callable

from adc_ui import config
from adc_ui.service import AdcService, Alert, Mode, Result, Role


class AdcApp(tk.Tk):
    def __init__(self, service: AdcService, show_simulator: bool = False) -> None:
        super().__init__()
        self.service = service
        self.state_snapshot = service.get_state()
        self.title(config.WINDOW_TITLE)
        self.geometry("1180x820")
        self.minsize(1024, 768)
        self.configure(background=config.COLORS["background"])
        self._events: queue.Queue[tuple[str, Any, Any]] = queue.Queue()
        self._executor = ThreadPoolExecutor(max_workers=4, thread_name_prefix="adc-ui")
        self._shown_screen = "Login"
        self._screen_after_id: str | None = None
        self._banner_status = ""
        self._last_touch = 0.0
        self._sound_queue: queue.Queue[str | None] = queue.Queue()
        self._sound_thread = threading.Thread(target=self._sound_worker, daemon=True)
        self._sound_thread.start()
        self._build_styles()
        self._build_shell()
        self.service.subscribe(lambda event: self._events.put(("event", event, None)))
        self.bind_all("<ButtonPress>", self._report_activity, add="+")
        self.bind_all("<KeyPress>", self._report_activity, add="+")
        self.bind_all("<Escape>", self._escape, add="+")
        self.protocol("WM_DELETE_WINDOW", self._close)
        self.refresh_state()
        if self.state_snapshot.active_alerts:
            self._play_alert_sound()
        self.show_screen("Starting Up")
        self.after(350, self._show_ready_screen)
        self.after(100, self._drain_events)
        self.after(1000, self._update_clock)
        if show_simulator:
            self.after(200, self.open_simulator)

    def _show_ready_screen(self) -> None:
        if self.winfo_exists():
            self.show_screen("Login")

    def _build_styles(self) -> None:
        style = ttk.Style(self)
        try:
            style.theme_use("clam")
        except tk.TclError:
            logging.warning("The preferred ttk theme is unavailable; using the platform default.")
        style.configure(".", font=config.FONTS["base"])
        style.configure("TFrame", background=config.COLORS["background"])
        style.configure("Surface.TFrame", background=config.COLORS["surface"])
        style.configure("TLabel", background=config.COLORS["background"], foreground=config.COLORS["text"])
        style.configure("Surface.TLabel", background=config.COLORS["surface"], foreground=config.COLORS["text"])
        style.configure("Heading.TLabel", font=config.FONTS["heading"], foreground=config.COLORS["text"])
        style.configure("Muted.TLabel", foreground=config.COLORS["muted"])
        style.configure(
            "Primary.TButton",
            font=config.FONTS["button"],
            padding=(18, 12),
            foreground=config.COLORS["primary_text"],
            background=config.COLORS["primary"],
        )
        style.map(
            "Primary.TButton",
            background=[("active", "#0b3658"), ("disabled", "#6e8190")],
            focuscolor=[("focus", config.COLORS["focus"])],
        )
        style.configure("Treeview", rowheight=36, font=config.FONTS["base"])
        style.configure("Treeview.Heading", font=("Segoe UI", 14, "bold"))

    def _build_shell(self) -> None:
        self.columnconfigure(0, weight=1)
        self.rowconfigure(2, weight=1)
        self.status = ttk.Frame(self, padding=(12, 8), style="Surface.TFrame")
        self.status.grid(row=0, column=0, sticky="ew")
        self.status.columnconfigure(3, weight=1)
        self.cabinet_label = ttk.Label(self.status, text=f"Cabinet: {config.CABINET_ID}", style="Surface.TLabel")
        self.cabinet_label.grid(row=0, column=0, padx=8)
        self.mode_label = ttk.Label(self.status, text="", style="Surface.TLabel")
        self.mode_label.grid(row=0, column=1, padx=10)
        self.comm_label = ttk.Label(self.status, text="", style="Surface.TLabel")
        self.comm_label.grid(row=0, column=2, padx=10)
        self.user_label = ttk.Label(self.status, text="Not logged in", style="Surface.TLabel")
        self.user_label.grid(row=0, column=3, sticky="w", padx=12)
        self.clock_label = ttk.Label(self.status, style="Surface.TLabel")
        self.clock_label.grid(row=0, column=4, padx=8)
        self.logout_button = ttk.Button(self.status, text="Log out", command=self._logout)
        self.logout_button.grid(row=0, column=5, padx=8)
        self.alert_bar = ttk.Frame(self, padding=(12, 8), style="Surface.TFrame")
        self.alert_bar.grid(row=1, column=0, sticky="ew")
        self.alert_bar.columnconfigure(0, weight=1)
        self.alert_label = ttk.Label(self.alert_bar, text="No active alerts", style="Surface.TLabel")
        self.alert_label.grid(row=0, column=0, sticky="w")
        self.alerts_button = ttk.Button(self.alert_bar, text="View all alerts", command=self.show_alerts)
        self.alerts_button.grid(row=0, column=1, padx=8)
        self.fault_button = ttk.Button(self.alert_bar, text="View fault", command=lambda: self.show_screen("Fault"))
        self.fault_button.grid(row=0, column=2, padx=8)
        self.content = ttk.Frame(self, padding=24)
        self.content.grid(row=2, column=0, sticky="nsew")
        self.content.columnconfigure(0, weight=1)
        self.content.rowconfigure(0, weight=1)
        self.feedback = tk.StringVar(value="")
        self._update_shell()

    def _update_shell(self) -> None:
        state = self.state_snapshot
        mode_icon = {Mode.NORMAL: "✓", Mode.MAINTENANCE: "⚙", Mode.FAULT: "!"}[state.mode]
        self.mode_label.configure(text=f"{mode_icon} MODE: {state.mode.value.upper()}")
        self.mode_label.configure(foreground={
            Mode.NORMAL: config.COLORS["success"],
            Mode.MAINTENANCE: config.COLORS["warning"],
            Mode.FAULT: config.COLORS["danger"],
        }[state.mode])
        self.comm_label.configure(
            text="Connected" if state.communication_connected else "Communication Lost",
            foreground=config.COLORS["success"] if state.communication_connected else config.COLORS["danger"],
        )
        user = state.current_user
        self.user_label.configure(
            text=f"{user.name} | {user.user_id} | {user.role.value}" if user else "Not logged in"
        )
        if user:
            self.logout_button.grid()
        else:
            self.logout_button.grid_remove()
        active = sorted(state.active_alerts, key=self._alert_priority)
        visible_banner = next(
            (alert for alert in active if alert.alert_type != "low_stock" or not alert.acknowledged),
            None,
        )
        self.alert_label.configure(text=visible_banner.message if visible_banner else "No active alerts")
        if visible_banner:
            self.alert_label.configure(foreground=(
                config.COLORS["danger"] if visible_banner.alert_type == "fault"
                else config.COLORS["warning"]
            ))
        else:
            self.alert_label.configure(foreground=config.COLORS["text"])
        self.fault_button.grid() if state.mode is Mode.FAULT else self.fault_button.grid_remove()

    @staticmethod
    def _alert_priority(alert: Alert) -> int:
        return {"fault": 0, "communication_loss": 1, "low_stock": 2}.get(alert.alert_type, 3)

    def _update_clock(self) -> None:
        self.clock_label.configure(text=datetime.now().strftime("%Y-%m-%d %H:%M:%S"))
        self.after(1000, self._update_clock)

    def _report_activity(self, _event: tk.Event[Any]) -> None:
        now = self.tk.call("clock", "milliseconds") / 1000
        if now - self._last_touch >= 1:
            self._last_touch = now
            self._submit(self.service.touch)

    def _escape(self, _event: tk.Event[Any]) -> str | None:
        if self._shown_screen not in {"Home", "Login", "Fault"}:
            self.show_screen("Home" if self.state_snapshot.current_user else "Login")
            return "break"
        return None

    def _close(self) -> None:
        self._sound_queue.put(None)
        self._executor.shutdown(wait=False, cancel_futures=True)
        self.destroy()

    def _drain_events(self) -> None:
        while True:
            try:
                kind, payload, callback = self._events.get_nowait()
            except queue.Empty:
                break
            if kind == "event":
                self.refresh_state()
                if payload.get("type") == "alert_raised":
                    self._play_alert_sound()
                if payload.get("type") == "location_closed" and self._shown_screen == "Dispense Waiting":
                    self.show_screen("Dispense Waiting")
            elif kind == "bell":
                self._play_bell_pattern(payload, 0)
            elif kind == "result":
                callback(payload)
        if self.winfo_exists():
            self.after(100, self._drain_events)

    def _submit(self, operation: Callable[..., Any], *args: Any, on_result: Callable[[Any], None] | None = None) -> None:
        def run() -> Any:
            return operation(*args)

        future: Future[Any] = self._executor.submit(run)

        def complete(done: Future[Any]) -> None:
            try:
                result = done.result()
            except Exception as exc:
                logging.exception("ADC service request failed")
                result = Result(False, f"Request failed: {exc}", "service_error")
            self._events.put(("result", result, on_result or self._show_result))

        future.add_done_callback(complete)

    def _show_result(self, result: Any) -> None:
        if isinstance(result, Result):
            self.feedback.set(result.message)
        self.refresh_state()

    def refresh_state(self) -> None:
        self.state_snapshot = self.service.get_state()
        self._update_shell()
        if self.state_snapshot.mode is Mode.FAULT and self.state_snapshot.current_user:
            if self._shown_screen not in {"Login", "Fault", "Alerts"}:
                self.show_screen("Fault")
            elif self._shown_screen == "Fault":
                self.show_screen("Fault")

    def show_screen(self, name: str) -> None:
        if self._screen_after_id is not None:
            self.after_cancel(self._screen_after_id)
            self._screen_after_id = None
        user = self.state_snapshot.current_user
        if name != "Login" and user is None:
            name = "Login"
        if self.state_snapshot.mode is Mode.FAULT and user and name not in {"Fault", "Alerts"}:
            name = "Fault"
        if name == "Home" and self.state_snapshot.mode is Mode.FAULT and user:
            name = "Fault"
        self._shown_screen = name
        self.feedback.set("")
        for child in self.content.winfo_children():
            child.destroy()
        screen = ttk.Frame(self.content)
        screen.grid(row=0, column=0, sticky="nsew")
        screen.columnconfigure(0, weight=1)
        getattr(self, f"_screen_{name.lower().replace(' ', '_')}")(screen)

    def _heading(self, parent: ttk.Frame, text: str, subtitle: str = "") -> None:
        ttk.Label(parent, text=text, style="Heading.TLabel").pack(anchor="w", pady=(0, 6))
        if subtitle:
            ttk.Label(parent, text=subtitle, wraplength=950, justify="left").pack(anchor="w", pady=(0, 14))

    def _message(self, parent: ttk.Frame) -> None:
        ttk.Label(parent, textvariable=self.feedback, wraplength=900, foreground=config.COLORS["danger"]).pack(
            anchor="w", pady=12
        )

    def _button(self, parent: ttk.Frame, label: str, command: Callable[[], None]) -> ttk.Button:
        button = ttk.Button(parent, text=label, command=command, style="Primary.TButton")
        button.pack(fill="x", pady=7, ipady=3)
        return button

    def _screen_login(self, parent: ttk.Frame) -> None:
        self._heading(parent, "Log in", "Scan your badge or enter your badge ID, then enter your PIN.")
        form = ttk.Frame(parent)
        form.pack(anchor="nw", fill="x", pady=10)
        ttk.Label(form, text="Badge ID").grid(row=0, column=0, sticky="w", pady=6)
        badge = ttk.Entry(form, width=32, font=config.FONTS["base"])
        badge.grid(row=1, column=0, sticky="w", pady=4)
        ttk.Label(form, text="PIN").grid(row=2, column=0, sticky="w", pady=(14, 6))
        pin = ttk.Entry(form, width=32, show="•", font=config.FONTS["base"])
        pin.grid(row=3, column=0, sticky="w", pady=4)
        pad = ttk.Frame(form)
        pad.grid(row=1, column=1, rowspan=3, padx=28)
        for index, digit in enumerate(("1", "2", "3", "4", "5", "6", "7", "8", "9", "Backspace", "0", "Clear")):
            row, col = divmod(index, 3)

            def insert(value: str = digit) -> None:
                if value == "Clear":
                    pin.delete(0, tk.END)
                elif value == "Backspace":
                    pin.delete(max(0, len(pin.get()) - 1), tk.END)
                else:
                    pin.insert(tk.END, value)

            ttk.Button(pad, text=digit, command=insert, width=12).grid(row=row, column=col, padx=3, pady=3, ipady=8)

        def login() -> None:
            button.configure(state="disabled")
            self.feedback.set("")
            badge_id, pin_value = badge.get().strip(), pin.get()
            pin.delete(0, tk.END)
            waiting_state = {"complete": False}
            self.after(
                500,
                lambda: self.feedback.set("Signing in…") if not waiting_state["complete"] else None,
            )

            def complete(result: Any) -> None:
                waiting_state["complete"] = True
                button.configure(state="normal")
                self.feedback.set(result.message if isinstance(result, Result) else "Request failed")
                self.refresh_state()
                if isinstance(result, Result) and result.ok:
                    self.show_screen("Home")

            self._submit(self.service.login, badge_id, pin_value, on_result=complete)

        badge.bind("<Return>", lambda _event: pin.focus_set())
        pin.bind("<Return>", lambda _event: login())
        button = ttk.Button(form, text="Log in", command=login, style="Primary.TButton")
        button.grid(row=4, column=0, sticky="w", pady=18, ipadx=24, ipady=6)
        self._message(parent)
        badge.focus_set()

    def _screen_starting_up(self, parent: ttk.Frame) -> None:
        self._heading(parent, "Starting up…", "Connecting to the cabinet service.")

    def _screen_home(self, parent: ttk.Frame) -> None:
        user = self.state_snapshot.current_user
        if user is None:
            self.show_screen("Login")
            return
        explanation = {
            Mode.NORMAL: "Normal mode: dispensing is available.",
            Mode.MAINTENANCE: "Maintenance mode: dispensing is unavailable; restocking may be available.",
            Mode.FAULT: "Fault mode: drawers are locked. Contact a technician.",
        }[self.state_snapshot.mode]
        self._heading(parent, f"Welcome, {user.name}", f"Current mode: {self.state_snapshot.mode.value}. {explanation}")
        actions = [
            ("Dispense Medication", "Dispense", {Role.NURSE, Role.PHARMACIST}),
            ("Restock", "Restock", {Role.PHARMACIST}),
            ("Inventory and Thresholds", "Inventory", {Role.PHARMACIST}),
            ("Event Log", "Event Log", {Role.PHARMACIST}),
            ("Users and Lockouts", "Users", {Role.PHARMACIST}),
            ("Mode Control", "Mode Control", {Role.PHARMACIST, Role.TECHNICIAN}),
            ("Fault Details and Clear Fault", "Fault", {Role.TECHNICIAN}),
        ]
        for label, destination, roles in actions:
            if user.role in roles or config.SHOW_ALL_ACTIONS:
                self._button(parent, label, lambda screen=destination: self.show_screen(screen))
        self._message(parent)

    def _screen_dispense(self, parent: ttk.Frame) -> None:
        self._heading(parent, "Dispense medication", "Select the medication and quantity, then request the drawer.")
        if self.state_snapshot.mode is not Mode.NORMAL:
            ttk.Label(
                parent,
                text=f"Cabinet is in {self.state_snapshot.mode.value} mode. The service will determine whether dispensing is allowed.",
                foreground=config.COLORS["warning"],
                wraplength=900,
            ).pack(anchor="w", pady=8)
        rows = self.state_snapshot.inventory
        choices = [f"{row['name']} — {row['location']}" for row in rows]
        selected = tk.StringVar(value=choices[0] if choices else "")
        ttk.Label(parent, text="Medication").pack(anchor="w")
        selector = ttk.Combobox(parent, textvariable=selected, values=choices, state="readonly", width=48)
        selector.pack(anchor="w", pady=(4, 14))
        quantity = self._quantity_control(parent)

        def dispense() -> None:
            if not choices:
                self.feedback.set("No medications are available.")
                return
            row = rows[choices.index(selected.get())]
            try:
                value = int(quantity.get())
            except ValueError:
                self.feedback.set("Enter a whole-number quantity.")
                return
            button.configure(state="disabled")

            def complete(result: Any) -> None:
                button.configure(state="normal")
                self.feedback.set(result.message)
                self.refresh_state()
                if isinstance(result, Result) and result.ok:
                    self.show_screen("Dispense Waiting")

            self._submit(self.service.request_dispense, row["medication_id"], value, on_result=complete)

        button = self._button(parent, "Dispense", dispense)
        self._message(parent)
        ttk.Button(parent, text="Back to Home", command=lambda: self.show_screen("Home")).pack(anchor="w", pady=10)

    def _screen_dispense_waiting(self, parent: ttk.Frame) -> None:
        transaction = self.state_snapshot.transaction
        if transaction is None:
            self._heading(parent, "Dispense complete", "The drawer is closed.")
            self._button(parent, "Done", lambda: self.show_screen("Home"))
            return
        self._heading(
            parent,
            f"Drawer for {transaction['medication']} (location {transaction['location']}) is unlocked.",
            f"Remove {transaction['quantity']} unit(s), then close the drawer.",
        )
        drawer_state = ttk.Label(
            parent,
            text="Drawer state: Open" if self.state_snapshot.drawer_open else "Drawer state: Closed",
        )
        drawer_state.pack(anchor="w", pady=8)
        elapsed_label = ttk.Label(parent)
        elapsed_label.pack(anchor="w")

        def update_elapsed() -> None:
            elapsed = max(0, int(datetime.now().timestamp() - transaction["started_at"]))
            elapsed_label.configure(text=f"Elapsed time: {elapsed} seconds")
            self._screen_after_id = self.after(1000, update_elapsed)

        update_elapsed()
        self._button(parent, "Done", lambda: self.show_screen("Home"))

    def _screen_restock(self, parent: ttk.Frame) -> None:
        self._heading(parent, "Restock", "Unlock a medication drawer, add stock, and submit the quantity.")
        rows = self.state_snapshot.inventory
        locations = [row["location"] for row in rows]
        selected = tk.StringVar(value=locations[0] if locations else "")
        ttk.Label(parent, text="Location").pack(anchor="w")
        selector = ttk.Combobox(parent, textvariable=selected, values=locations, state="readonly", width=40)
        selector.pack(anchor="w", pady=(4, 12))
        quantity = self._quantity_control(parent)
        quantity.configure(state="disabled")

        def begin() -> None:
            button.configure(state="disabled")

            def complete(result: Any) -> None:
                button.configure(state="normal")
                self.feedback.set(result.message)
                self.refresh_state()
                if isinstance(result, Result) and result.ok:
                    quantity.configure(state="normal")
                    selector.configure(state="disabled")
                    instructions.configure(
                        text=f"Drawer {selected.get()} unlocked. Add the medication, enter the quantity added, then close the drawer."
                    )
                    button.configure(text="Submit quantity", command=submit)

            self._submit(self.service.begin_restock, selected.get(), on_result=complete)

        def submit() -> None:
            try:
                amount = int(quantity.get())
            except ValueError:
                self.feedback.set("Enter a whole-number quantity.")
                return
            button.configure(state="disabled")

            def complete(result: Any) -> None:
                button.configure(state="normal")
                self.feedback.set(result.message)
                self.refresh_state()
                if isinstance(result, Result) and result.ok:
                    self._button(parent, "Restock another", lambda: self.show_screen("Restock"))

            self._submit(self.service.submit_restock_quantity, amount, on_result=complete)

        button = self._button(parent, "Unlock drawer", begin)
        instructions = ttk.Label(parent, text="", wraplength=900)
        instructions.pack(anchor="w", pady=6)
        self._message(parent)
        ttk.Button(parent, text="Back to Home", command=lambda: self.show_screen("Home")).pack(anchor="w", pady=10)

    def _quantity_control(self, parent: ttk.Frame) -> ttk.Entry:
        frame = ttk.Frame(parent)
        ttk.Label(frame, text="Quantity").pack(anchor="w")
        controls = ttk.Frame(frame)
        controls.pack(anchor="w", pady=5)
        value = tk.StringVar(value="1")
        entry = ttk.Entry(controls, textvariable=value, width=8, justify="center")
        entry.pack(side="left", ipady=8)

        def adjust(amount: int) -> None:
            try:
                current = int(value.get())
            except ValueError:
                current = 1
            value.set(str(max(1, current + amount)))

        ttk.Button(controls, text="−", command=lambda: adjust(-1), width=5).pack(side="left", padx=5, ipady=5)
        ttk.Button(controls, text="+", command=lambda: adjust(1), width=5).pack(side="left", ipady=5)
        frame.pack(anchor="w", pady=(0, 12))
        return entry

    def _screen_inventory(self, parent: ttk.Frame) -> None:
        self._heading(parent, "Inventory and thresholds", "Low-stock status is active when quantity is below the threshold.")
        columns = ("name", "location", "quantity", "threshold", "status")
        tree = ttk.Treeview(parent, columns=columns, show="headings", height=10, selectmode="browse")
        for col, title, width in zip(columns, ("Medication", "Location", "Recorded quantity", "Threshold", "Status"), (260, 200, 190, 160, 120)):
            tree.heading(col, text=title)
            tree.column(col, width=width, anchor="w")
        tree.pack(fill="both", expand=True, pady=10)
        rows = self.state_snapshot.inventory
        by_id: dict[str, dict[str, Any]] = {}
        for row in rows:
            by_id[row["medication_id"]] = row
            tree.insert("", "end", iid=row["medication_id"], values=(
                row["name"], row["location"], row["quantity"], row["threshold"],
                "⚠ LOW" if row["low"] else "✓ OK",
            ))
        threshold = ttk.Entry(parent, width=18)
        threshold.pack(anchor="w", pady=6)

        def load_selected(_event: tk.Event[Any] | None = None) -> None:
            selection = tree.selection()
            if selection:
                threshold.delete(0, tk.END)
                threshold.insert(0, str(by_id[selection[0]]["threshold"]))

        tree.bind("<<TreeviewSelect>>", load_selected)

        def save() -> None:
            selection = tree.selection()
            if not selection:
                self.feedback.set("Select a medication first.")
                return
            button.configure(state="disabled")

            def complete(result: Any) -> None:
                button.configure(state="normal")
                self.feedback.set(result.message)
                if isinstance(result, Result) and result.ok:
                    self.show_screen("Inventory")
                    self.feedback.set(result.message)
                else:
                    self.refresh_state()

            self._submit(self.service.set_threshold, selection[0], threshold.get(), on_result=complete)

        button = ttk.Button(parent, text="Save threshold", command=save, style="Primary.TButton")
        button.pack(anchor="w", pady=8, ipadx=12, ipady=6)
        ttk.Button(parent, text="Back to Home", command=lambda: self.show_screen("Home")).pack(anchor="w")
        self._message(parent)

    def _screen_event_log(self, parent: ttk.Frame) -> None:
        self._heading(parent, "Event log", "Read-only. Newest events appear first.")
        self._tree(parent, ("timestamp", "event_type", "user_id", "details"),
                   ("Timestamp", "Event type", "User ID", "Details"), self.service.get_event_log())
        self._button(parent, "Refresh", lambda: self.show_screen("Event Log"))
        ttk.Button(parent, text="Back to Home", command=lambda: self.show_screen("Home")).pack(anchor="w")

    def _screen_users(self, parent: ttk.Frame) -> None:
        self._heading(parent, "Users and lockouts", "Select a locked-out user to request a lockout override.")
        columns = ("user_id", "name", "role", "status")
        tree = ttk.Treeview(parent, columns=columns, show="headings", height=10, selectmode="browse")
        for col, title, width in zip(columns, ("User ID", "Name", "Role", "Status"), (150, 260, 180, 400)):
            tree.heading(col, text=title)
            tree.column(col, width=width, anchor="w")
        tree.pack(fill="both", expand=True, pady=10)
        for row in self.service.get_users():
            tree.insert("", "end", iid=row["user_id"], values=(row["user_id"], row["name"], row["role"], row["status"]))

        def override() -> None:
            selection = tree.selection()
            if not selection:
                self.feedback.set("Select a user first.")
                return
            button.configure(state="disabled")

            def complete(result: Any) -> None:
                button.configure(state="normal")
                self.feedback.set(result.message)
                if isinstance(result, Result) and result.ok:
                    self.show_screen("Users")
                    self.feedback.set(result.message)
                else:
                    self.refresh_state()

            self._submit(self.service.override_lockout, selection[0], on_result=complete)

        button = self._button(parent, "Override lockout", override)
        self._message(parent)
        ttk.Button(parent, text="Back to Home", command=lambda: self.show_screen("Home")).pack(anchor="w")

    def _screen_mode_control(self, parent: ttk.Frame) -> None:
        mode = self.state_snapshot.mode
        self._heading(parent, "Mode control", f"Current mode: {mode.value}. {self._mode_explanation(mode)}")
        if mode is Mode.FAULT:
            ttk.Label(parent, text="Open Fault Details and Clear Fault to continue.", foreground=config.COLORS["danger"]).pack(anchor="w")
        else:
            target = Mode.MAINTENANCE if mode is Mode.NORMAL else Mode.NORMAL
            button = self._button(parent, f"Switch to {target.value}", lambda: self._change_mode(target))
            self._mode_button = button
        self._message(parent)
        ttk.Button(parent, text="Back to Home", command=lambda: self.show_screen("Home")).pack(anchor="w")

    @staticmethod
    def _mode_explanation(mode: Mode) -> str:
        return {
            Mode.NORMAL: "Dispensing is available.",
            Mode.MAINTENANCE: "Dispensing is unavailable; restocking is available to pharmacists.",
            Mode.FAULT: "The cabinet is locked pending technician review.",
        }[mode]

    def _change_mode(self, target: Mode) -> None:
        self._mode_button.configure(state="disabled")

        def complete(result: Any) -> None:
            self._mode_button.configure(state="normal")
            self.feedback.set(result.message)
            self.refresh_state()
            if isinstance(result, Result) and result.ok:
                self.show_screen("Mode Control")
                self.feedback.set(result.message)

        self._submit(self.service.request_mode, target, on_result=complete)

    def _screen_fault(self, parent: ttk.Frame) -> None:
        self._heading(parent, "Fault details", "Cabinet is in Fault mode. Contact a technician.")
        if self.state_snapshot.faults:
            columns = ("cause", "location", "raised_at")
            tree = ttk.Treeview(parent, columns=columns, show="headings", height=7)
            for col, title, width in zip(columns, ("Fault cause", "Location", "Time raised"), (430, 250, 250)):
                tree.heading(col, text=title)
                tree.column(col, width=width, anchor="w")
            for index, fault in enumerate(self.state_snapshot.faults):
                tree.insert("", "end", iid=str(index), values=(fault["cause"], fault["location"], fault["raised_at"]))
            tree.pack(fill="x", pady=10)
        user = self.state_snapshot.current_user
        if user and (user.role is Role.TECHNICIAN or config.SHOW_ALL_ACTIONS):
            button = self._button(parent, "Clear fault", self._clear_fault)
            self._fault_clear_button = button
        self._message(parent)
        if not user:
            ttk.Button(parent, text="Log in", command=lambda: self.show_screen("Login")).pack(anchor="w")
        elif user.role is not Role.TECHNICIAN and not config.SHOW_ALL_ACTIONS:
            ttk.Label(parent, text="Cabinet is in Fault mode. Contact a technician.").pack(anchor="w", pady=8)
        else:
            ttk.Button(parent, text="Back to Home", command=lambda: self.show_screen("Home")).pack(anchor="w")

    def _clear_fault(self) -> None:
        self._fault_clear_button.configure(state="disabled")

        def complete(result: Any) -> None:
            self._fault_clear_button.configure(state="normal")
            self.feedback.set(result.message)
            self.refresh_state()
            if isinstance(result, Result) and result.ok:
                self.show_screen("Home")
                self.feedback.set(result.message)

        self._submit(self.service.clear_fault, on_result=complete)

    def _tree(
        self, parent: ttk.Frame, columns: tuple[str, ...], titles: tuple[str, ...],
        rows: list[dict[str, Any]],
    ) -> ttk.Treeview:
        container = ttk.Frame(parent)
        container.pack(fill="both", expand=True)
        tree = ttk.Treeview(container, columns=columns, show="headings", height=12)
        scrollbar = ttk.Scrollbar(container, orient="vertical", command=tree.yview)
        tree.configure(yscrollcommand=scrollbar.set)
        for column, title in zip(columns, titles):
            tree.heading(column, text=title)
            tree.column(column, width=220 if column != "details" else 520, anchor="w")
        for index, row in enumerate(rows):
            tree.insert("", "end", iid=str(index), values=tuple(row.get(column, "") for column in columns))
        tree.pack(side="left", fill="both", expand=True)
        scrollbar.pack(side="right", fill="y")
        return tree

    def _screen_alerts(self, parent: ttk.Frame) -> None:
        self._heading(parent, "Active alerts", "Fault alerts cannot be dismissed. Communication alerts clear when restored.")
        for alert in sorted(self.state_snapshot.active_alerts, key=self._alert_priority):
            item = ttk.Frame(parent)
            item.pack(fill="x", pady=7)
            ttk.Label(item, text=f"{alert.alert_type.replace('_', ' ').title()} — {alert.message}", wraplength=800).pack(side="left", anchor="w")
            ttk.Label(item, text=alert.raised_at).pack(side="left", padx=12)
            if alert.alert_type == "low_stock" and not alert.acknowledged:
                ttk.Button(item, text="Acknowledge", command=lambda alert_id=alert.alert_id: self._acknowledge(alert_id)).pack(side="right")
        ttk.Button(parent, text="Back", command=lambda: self.show_screen("Home" if self.state_snapshot.current_user else "Login")).pack(anchor="w", pady=14)

    def _acknowledge(self, alert_id: str) -> None:
        self._submit(self.service.acknowledge_alert, alert_id)

    def show_alerts(self) -> None:
        if self.state_snapshot.current_user is None:
            self._shown_screen = "Alerts"
            for child in self.content.winfo_children():
                child.destroy()
            screen = ttk.Frame(self.content)
            screen.grid(row=0, column=0, sticky="nsew")
            self._screen_alerts(screen)
        else:
            self.show_screen("Alerts")

    def _logout(self) -> None:
        self._submit(self.service.logout, on_result=lambda _result: self.show_screen("Login"))

    def _play_alert_sound(self) -> None:
        if not self.state_snapshot.active_alerts:
            return
        alert = min(self.state_snapshot.active_alerts, key=self._alert_priority)
        self._sound_queue.put(alert.alert_type)

    def _sound_worker(self) -> None:
        while True:
            sound_type = self._sound_queue.get()
            if sound_type is None:
                return
            pattern = config.SOUND_PATTERNS.get(sound_type, [])
            if sys.platform == "win32":
                try:
                    import winsound
                    for frequency, duration in pattern:
                        winsound.Beep(frequency, duration)
                except (ImportError, OSError, RuntimeError):
                    logging.warning("Alert sound could not be played on this system.")
            else:
                self._events.put(("bell", pattern, None))

    def _play_bell_pattern(self, pattern: list[tuple[int, int]], index: int) -> None:
        if index >= len(pattern):
            return
        try:
            self.bell()
        except tk.TclError:
            logging.warning("No alert sound output is available.")
            return
        self.after(pattern[index][1], lambda: self._play_bell_pattern(pattern, index + 1))

    def open_simulator(self) -> None:
        if hasattr(self, "_simulator") and self._simulator.winfo_exists():
            self._simulator.lift()
            return
        simulator = tk.Toplevel(self)
        simulator.title("ADC Developer Simulator")
        simulator.geometry("460x360")
        self._simulator = simulator
        frame = ttk.Frame(simulator, padding=18)
        frame.pack(fill="both", expand=True)
        ttk.Label(frame, text="Development simulator", style="Heading.TLabel").pack(anchor="w", pady=(0, 12))
        ttk.Label(frame, text="Demonstration backend controls; not connected to cabinet hardware.").pack(anchor="w")
        ttk.Button(
            frame, text="Monitoring link: toggle connection",
            command=lambda: self._toggle_connection(),
        ).pack(fill="x", pady=8, ipady=6)
        ttk.Button(
            frame, text="Raise simulated lock fault",
            command=lambda: self._simulate_fault(),
        ).pack(fill="x", pady=8, ipady=6)
        ttk.Button(
            frame, text="Close open drawer",
            command=lambda: self._simulator_close_drawer(),
        ).pack(fill="x", pady=8, ipady=6)

    def _toggle_connection(self) -> None:
        setter = getattr(self.service, "set_communication", None)
        if setter:
            setter(not self.state_snapshot.communication_connected)

    def _simulate_fault(self) -> None:
        simulator = getattr(self.service, "simulate_fault", None)
        if simulator:
            simulator()

    def _simulator_close_drawer(self) -> None:
        close = getattr(self.service, "simulate_drawer_close", None)
        if close:
            close()
