import smbus
import time
import os
from datetime import datetime
from rich.console import Console
from rich.panel import Panel
from rich.live import Live

console = Console()

ADDR = 0x2d
LOW_VOL = 2500
MAX_BATTERY_VOLTAGE = 4200
MAX_BATTERY_CURRENT = 1000
MAX_VBUS_VOLTAGE = 20000
MAX_VBUS_CURRENT = 3000
MAX_PERCENTAGE = 100
MAX_BATTERY_VOLTAGE_TOTAL = 16800

THEME = {
    "battery": "bright_green",
    "cell": "cyan",
    "input": "white",
    "warning": "red",
    "info": "blue",
    "status": "magenta",
    "header": "bright_magenta"
}

low_count = 0
bus = smbus.SMBus(1)

COLOR_RESET = "\033[0m"
COLOR_GREEN = "\033[32m"
COLOR_RED = "\033[31m"
COLOR_YELLOW = "\033[33m"
COLOR_CYAN = "\033[36m"
COLOR_BLUE = "\033[34m"
COLOR_PURPLE = "\033[35m"


def generate_bar(value, max_value, width=20, color=COLOR_BLUE, unit=""):
    blocks = ["⠄", "⠅", "⠇", "⠍", "⠶", "⠫", "⠾", "⠷"]
    value = max(0, min(value, max_value))
    full_blocks = int((value / max_value) * width)
    remainder_ratio = (value / max_value) * width - full_blocks
    partial_block_index = int(remainder_ratio * 8)
    bar = (
        f"{color}" +
        "⣿" * full_blocks +
        (blocks[partial_block_index] if partial_block_index > 0 else "") +
        "." * (width - full_blocks - (1 if partial_block_index > 0 else 0)) +
        f"{COLOR_RESET}"
    )
    value_str = f"{value:.1f} {unit}".strip()
    return f"{bar} {value_str:>10}"


def get_cpu_temperature():
    try:
        with open('/sys/class/thermal/thermal_zone0/temp', 'r') as f:
            temp = int(f.read()) / 1000.0
        return round(temp, 1)
    except:
        return None


def get_health_indicator(voltages, current, percentage):
    max_v = max(voltages)
    min_v = min(voltages)
    imbalance = (max_v - min_v) / max_v * 100
    if imbalance > 10:
        return ("⚠️ Poor (Voltage imbalance)", "red")
    elif current < -500:
        return ("⚠️ Fair (High discharge)", "yellow")
    elif percentage < 20:
        return ("⚠️ Caution (Low charge)", "yellow")
    else:
        return ("✓ Good", "green")


def format_simple_output(title, lines, style="white"):
    content = f"[bold {style}]{title}[/bold {style}]\n" + "\n".join(lines)
    return Panel(content, border_style=style)


def make_battery_info(data):
    battery_voltage = (data[0] | data[1] << 8)
    current = (data[2] | data[3] << 8)
    if current > 0x7FFF:
        current -= 0xFFFF
    battery_percent = int(data[4] | data[5] << 8)
    remaining_capacity = data[6] | data[7] << 8
    run_time_to_empty = data[8] | data[9] << 8
    time_to_full = data[10] | data[11] << 8

    current_abs = abs(current)
    current_color = COLOR_GREEN if current >= 0 else COLOR_PURPLE
    current_bar = generate_bar(current_abs, MAX_BATTERY_CURRENT, color=current_color, unit="mA")
    percent_bar = generate_bar(battery_percent, MAX_PERCENTAGE, color=COLOR_GREEN, unit="%")
    output_power = round(current_abs / 1000 * battery_voltage / 1000, 2)
    battery_power = round(remaining_capacity / 1000 * battery_voltage / 1000, 2)

    health_status, _ = get_health_indicator([battery_voltage], current, battery_percent)

    lines = [
        f"Voltage:   {generate_bar(battery_voltage, MAX_BATTERY_VOLTAGE_TOTAL, color=COLOR_RED, unit='mV')}",
        f"Current:   {current_bar}",
        f"Percent:   {percent_bar}",
        f"Remaining: {remaining_capacity} mAh / {battery_power} Wh",
        f"Output:    {output_power} W",
        f"Estimate:  {'%d min to empty' % run_time_to_empty if current < 0 else '%d min to full' % time_to_full}",
        f"Health:    {health_status}"
    ]
    return format_simple_output("Battery", lines), current


def make_cell_info(data):
    voltages = [
        (data[0] | data[1] << 8),
        (data[2] | data[3] << 8),
        (data[4] | data[5] << 8),
        (data[6] | data[7] << 8)
    ]
    lines = [f"Cell {i+1}: {generate_bar(v, MAX_BATTERY_VOLTAGE, color=COLOR_CYAN, unit='mV')}" for i, v in enumerate(voltages)]
    return format_simple_output("Cells", lines, style=THEME["cell"]), voltages


def make_power_info(data):
    vbus_voltage = data[0] | data[1] << 8
    vbus_current = data[2] | data[3] << 8
    vbus_power = (data[4] | data[5] << 8) / 1000
    lines = [
        f"VBUS Volt:  {generate_bar(vbus_voltage, MAX_VBUS_VOLTAGE, color=COLOR_YELLOW)}",
        f"VBUS Curr:  {generate_bar(vbus_current, MAX_VBUS_CURRENT, color=COLOR_YELLOW, unit='mA')}",
        f"VBUS Power: {vbus_power} W"
    ]
    return format_simple_output("Input Power", lines, style=THEME["input"])


def check_shutdown_condition(voltages, current):
    return any(v < LOW_VOL for v in voltages) and current < 50

charging_state_map = {
    0x40: ("⚡ Fast Charging", "green"),
    0x80: ("🔋 Charging", "green"),
    0x20: ("🔌 Discharging", "yellow"),
    0x00: ("💤 Idle", "cyan")
}

def get_charging_state(data_byte):
    if (data_byte & 0x40):
        return charging_state_map[0x40]
    elif (data_byte & 0x80):
        return charging_state_map[0x80]
    else:
        return charging_state_map[0x20]

def main():
    global low_count
    with Live(console=console, refresh_per_second=2, screen=False) as live:
        while True:
            try:
                data = bus.read_i2c_block_data(ADDR, 0x02, 0x01)
                state_text, state_style = get_charging_state(data[0])
                cpu_tmp = get_cpu_temperature()
                timestamp = datetime.now().strftime('%Y-%m-%d %H:%M:%S')

                header = f"[bold]{timestamp}[/bold] | Status: [{state_style}]{state_text}[/{state_style}] | CPU: {cpu_tmp}°C"
                console.clear()
                console.print(header)

                data = bus.read_i2c_block_data(ADDR, 0x10, 0x06)
                power_panel = make_power_info(data)
                console.print(power_panel)

                data = bus.read_i2c_block_data(ADDR, 0x20, 0x0C)
                battery_panel, current = make_battery_info(data)
                console.print(battery_panel)

                data = bus.read_i2c_block_data(ADDR, 0x30, 0x08)
                cell_panel, voltages = make_cell_info(data)
                console.print(cell_panel)

                if check_shutdown_condition(voltages, current):
                    low_count += 1
                    if low_count >= 3:
                        warning = Panel("⚠️ WARNING: Low battery voltage detected! System will shutdown soon...", border_style="red")
                        console.print(warning)
                else:
                    low_count = 0

            except Exception as e:
                error_panel = Panel(f"Error reading battery data: {str(e)}", title="ERROR", border_style="red")
                console.print(error_panel)

            time.sleep(0.5)

if __name__ == "__main__":
    try:
        main()
    except KeyboardInterrupt:
        console.print("\n[bold yellow]Monitoring stopped by user[/bold yellow]")
    except Exception as e:
        console.print(f"[bold red]Fatal error: {str(e)}[/bold red]")
