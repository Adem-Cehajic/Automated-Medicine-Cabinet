# Automated Medicine Cabinet
3K04

## Python/Tkinter front end

The local cabinet interface is a Python 3.10+ Tkinter application. Run it from
the repository root with:

```powershell
python main.py
```

For development controls (simulated monitoring connection, lock fault, and
drawer closure), run:

```powershell
python main.py --simulator
```

The demo service starts in Fault mode. Demo sign-ins are `N001` / `1234`
(Nurse), `P001` / `2468` (Pharmacist), and `T001` / `1357` (Technician).
After signing in as the technician, clear the startup fault before switching
to Normal mode. The in-memory `DemoAdcService` in `adc_ui/service.py` is only a
demonstration adapter; connect the production cabinet backend by implementing
the `AdcService` protocol. The UI sends requests to that service asynchronously
and displays its response messages without rewriting denials.

Run the standard-library service tests with:

```powershell
python -m unittest discover -s tests
```
