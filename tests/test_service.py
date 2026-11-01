import unittest

from adc_ui.service import DemoAdcService, Mode


class DemoAdcServiceTests(unittest.TestCase):
    def setUp(self) -> None:
        self.service = DemoAdcService()

    def sign_in(self, badge_id: str, pin: str) -> None:
        result = self.service.login(badge_id, pin)
        self.assertTrue(result.ok, result.message)

    def test_startup_is_fault_and_technician_clears_to_maintenance(self) -> None:
        self.assertEqual(self.service.get_state().mode, Mode.FAULT)
        self.sign_in("T001", "1357")
        result = self.service.clear_fault()
        self.assertTrue(result.ok)
        self.assertEqual(self.service.get_state().mode, Mode.MAINTENANCE)

    def test_login_locks_account_on_third_incorrect_pin(self) -> None:
        for _ in range(2):
            result = self.service.login("N001", "0000")
            self.assertEqual(result.message, "Incorrect PIN")
        result = self.service.login("N001", "0000")
        self.assertEqual(result.message, "Account locked for 30 minutes")
        self.assertEqual(self.service.login("N001", "1234").message, "Account locked")

    def test_technician_dispense_is_denied_by_service(self) -> None:
        self.sign_in("T001", "1357")
        result = self.service.request_dispense("med-001", 1)
        self.assertFalse(result.ok)
        self.assertEqual(result.message, "You do not have permission to do this")

    def test_dispense_updates_stock_only_after_drawer_closes(self) -> None:
        self.sign_in("T001", "1357")
        self.assertTrue(self.service.clear_fault().ok)
        self.assertTrue(self.service.request_mode(Mode.NORMAL).ok)
        self.sign_in_out()
        self.sign_in("N001", "1234")
        before = self.service.get_inventory()[0]["quantity"]
        result = self.service.request_dispense("med-001", 2)
        self.assertTrue(result.ok)
        self.assertEqual(self.service.get_inventory()[0]["quantity"], before)
        self.service.simulate_drawer_close()
        self.assertEqual(self.service.get_inventory()[0]["quantity"], before - 2)

    def test_pharmacist_restock_requires_maintenance(self) -> None:
        self.sign_in("T001", "1357")
        self.assertTrue(self.service.clear_fault().ok)
        self.sign_in_out()
        self.sign_in("P001", "2468")
        result = self.service.begin_restock("Drawer A1")
        self.assertTrue(result.ok)
        result = self.service.submit_restock_quantity(3)
        self.assertTrue(result.ok)
        self.assertEqual(self.service.get_inventory()[0]["quantity"], 15)

    def test_low_stock_is_only_raised_below_threshold_and_acknowledgement_is_panel_only(self) -> None:
        self.assertFalse(any(alert.alert_id == "low_stock:med-003" for alert in self.service.get_state().active_alerts))
        self.sign_in("T001", "1357")
        self.assertTrue(self.service.clear_fault().ok)
        self.assertTrue(self.service.request_mode(Mode.NORMAL).ok)
        self.sign_in_out()
        self.sign_in("N001", "1234")
        self.assertTrue(self.service.request_dispense("med-003", 1).ok)
        self.service.simulate_drawer_close()
        alert = next(alert for alert in self.service.get_state().active_alerts if alert.alert_id == "low_stock:med-003")
        self.service.acknowledge_alert(alert.alert_id)
        acknowledged = next(
            item for item in self.service.get_state().active_alerts if item.alert_id == alert.alert_id
        )
        self.assertTrue(acknowledged.acknowledged)

    def test_pin_is_never_written_to_event_log(self) -> None:
        self.service.login("N001", "0000")
        self.assertNotIn("0000", str(self.service.get_event_log()))

    def sign_in_out(self) -> None:
        self.assertTrue(self.service.logout().ok)


if __name__ == "__main__":
    unittest.main()
