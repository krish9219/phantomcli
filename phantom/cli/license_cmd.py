"""``phantom license`` Typer subcommand — activate, status, deactivate, devices."""

from __future__ import annotations

import json as _json

import typer

from phantom import licensing

__all__ = ["license_app"]

license_app = typer.Typer(no_args_is_help=True, help="Manage your Phantom Pro licence.")


@license_app.command("status", help="Show current licence state.")
def cmd_status(json_output: bool = typer.Option(False, "--json", help="emit JSON")) -> None:
    s = licensing.license_status()
    if json_output:
        typer.echo(_json.dumps(s.to_dict()))
        return

    if s.tier == "pro":
        if s.reason == "grandfathered":
            typer.echo("\033[1;32m✔ Phantom Pro\033[0m  (grandfathered — pre-gate install)")
        elif s.reason == "licensed_offline_grace":
            typer.echo(f"\033[1;32m✔ Phantom Pro\033[0m  (offline grace — re-validate when online)")
            if s.email: typer.echo(f"  Licensed to: {s.email}")
        else:
            typer.echo(f"\033[1;32m✔ Phantom Pro\033[0m  (licensed)")
            if s.email: typer.echo(f"  Licensed to: {s.email}")
            if s.devices_used is not None and s.max_devices is not None:
                typer.echo(f"  Devices:     {s.devices_used} / {s.max_devices}")
    elif s.tier == "trial":
        typer.echo(f"\033[1;36m◷ Phantom Pro · trial\033[0m")
        typer.echo(f"  {s.days_remaining} day(s) remaining of {licensing.TRIAL_DAYS}")
        typer.echo(f"  Buy a lifetime licence: https://phantom.aravindlabs.tech/buy")
    else:
        typer.echo(f"\033[1;33m✗ Phantom Free\033[0m  ({s.reason})")
        typer.echo(f"  Pro features (serve, swarm, dictate, self-dev) are locked.")
        typer.echo(f"  Buy ₹999 lifetime: https://phantom.aravindlabs.tech/buy")


@license_app.command("activate", help="Activate a Pro licence key on this machine.")
def cmd_activate(
    key: str = typer.Argument(..., help="PHC-XXXXXXXX-XXXXXXXX-XXXXXXXX"),
) -> None:
    try:
        s = licensing.activate(key)
    except ValueError as e:
        typer.echo(f"\033[1;31m✗ {e}\033[0m", err=True)
        raise typer.Exit(2)
    except RuntimeError as e:
        typer.echo(f"\033[1;31m✗ {e}\033[0m", err=True)
        raise typer.Exit(1)

    typer.echo(f"\033[1;32m✔ Phantom Pro activated\033[0m")
    if s.email: typer.echo(f"  Licensed to: {s.email}")
    if s.devices_used is not None and s.max_devices is not None:
        typer.echo(f"  Devices:     {s.devices_used} / {s.max_devices}")


@license_app.command("deactivate", help="Remove this device from your licence.")
def cmd_deactivate(
    confirm: bool = typer.Option(False, "--yes", "-y", help="skip confirmation"),
) -> None:
    if not confirm:
        if not typer.confirm("Deactivate this device? Pro features will lock until re-activated."):
            raise typer.Exit(0)
    if licensing.deactivate():
        typer.echo("\033[1;32m✔ This device has been removed from your licence.\033[0m")
    else:
        typer.echo("No active licence on this device.")


@license_app.command("devices", help="List devices registered to this licence.")
def cmd_devices(
    json_output: bool = typer.Option(False, "--json", help="emit JSON"),
) -> None:
    devs = licensing.list_devices()
    if json_output:
        typer.echo(_json.dumps(devs))
        return
    if not devs:
        typer.echo("No devices found (or no licence activated).")
        return
    typer.echo("Registered devices:")
    for d in devs:
        typer.echo(f"  • {d.get('device_name','?')}  [{d.get('platform','?')}]  last seen {d.get('last_seen','?')}")
