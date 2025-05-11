import smbus
import time
import os
from datetime import datetime
from rich.console import Console
from rich.panel import Panel
from rich.table import Table
from rich.progress import Progress, BarColumn, TextColumn
from rich.live import Live
from rich.layout import Layout


console = Console()

ADDR                      = 0x2d
LOW_VOL                   = 2500   
MAX_BATTERY_VOLTAGE       = 4200  
MAX_BATTERY_CURRENT       = 1000  
MAX_VBUS_VOLTAGE          = 20000 
MAX_VBUS_CURRENT          = 3000  
MAX_PERCENTAGE            = 100   
MAX_BATTERY_VOLTAGE_TOTAL = 16800


THEME = {
    "battery": "bright_green",
    "cell": "cyan",
    "input": "yellow",
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
COLOR_BOLD = "\033[1m"


def generate_bar(value, max_value, width=40, color=COLOR_BLUE, unit=""):

    blocks = ["⠄", "⠅", "⠇", "⠍", "⠶", "⠫", "⠾", "⠷"]
    
    value = max(0, min(value, max_value))
    
    full_blocks = int((value / max_value) * width)
    
    remainder_ratio = (value / max_value) * width - full_blocks
    partial_block_index = int(remainder_ratio * 8)
    
    bar = (
        f"{color}" 
        + "⣿" * full_blocks  
        + (blocks[partial_block_index] if partial_block_index > 0 else "")  
        + "." * (width - full_blocks - (1 if partial_block_index > 0 else 0)) 
        + f"{COLOR_RESET}" 
    )
    
    value_str = f"{value:.1f} {unit}".strip()
    
    return f"{bar} {value_str:>10}"



def make_status_panel(title, data_rows, style="cyan", icon="🔋"):
    table = Table(show_header=False, expand=True, show_lines=True)
    table.add_column("Parameter", style=f"bold {style}", width=20)
    table.add_column("Value", style=style, width=60)
    for label, value in data_rows:
        table.add_row(f"{icon} {label}", value)
    return Panel(
        table,
        title=f"[bold {style}]{title}[/bold {style}]",
        border_style=style,
        padding=(1, 2),
        subtitle_align="right",
        style=f"dim {style}"
    )


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
        return ("⚠️ [bold red]Poor[/bold red] (Voltage imbalance)", "red")
    elif current < -500: 
        return ("⚠️ [bold yellow]Fair[/bold yellow] (High discharge)", "yellow")
    elif percentage < 20:
        return ("⚠️ [bold yellow]Caution[/bold yellow] (Low charge)", "yellow")
    else:
        return ("✓ [bold green]Good[/bold green]", "green")




def make_battery_panel(data):
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

    output_power = round(current_abs / 1000 * battery_voltage / 1000, 4)
    battery_power = round(remaining_capacity / 1000 * battery_voltage / 1000, 4)
    

    rows = [
        ("Voltage", generate_bar(battery_voltage, MAX_BATTERY_VOLTAGE_TOTAL, color=COLOR_GREEN)),
        ("Current", current_bar),
        ("Percentage", percent_bar),
        ("Remaining Capacity", f"{remaining_capacity} mAh   {battery_power}wh".rjust(10)),
        ("battery power", f"{output_power}w ".rjust(10)),
        ("Time Estimate",f"{run_time_to_empty} min to empty".rjust(20) if current < 0 else f"{time_to_full} min to full".rjust(20))
    ]
    
    health_status, health_style = get_health_indicator([battery_voltage], current, battery_percent)
    rows.insert(2, ("Health Status", f"[{health_style}]{health_status}[/{health_style}]".rjust(20)))
    
    return make_status_panel("Battery Status", rows, style=THEME["battery"]), current

def make_cell_voltages_panel(data):
    voltages = [
        (data[0] | data[1] << 8) ,
        (data[2] | data[3] << 8),
        (data[4] | data[5] << 8),
        (data[6] | data[7] << 8)
    ]
    rows = [(f"Cell {i+1}", generate_bar(v, MAX_BATTERY_VOLTAGE, color=COLOR_CYAN)) for i, v in enumerate(voltages)]
    return make_status_panel("Cell Voltages", rows, style=THEME["cell"], icon="⚡"), voltages

def make_power_panel(data):
    vbus_voltage = data[0] | data[1] << 8
    vbus_current = data[2] | data[3] << 8
    vbus_power =( data[4] | data[5] << 8)/1000
    rows = [
        ("VBUS Voltage", generate_bar(vbus_voltage, MAX_VBUS_VOLTAGE, color=COLOR_YELLOW)),
        ("VBUS Current", generate_bar(vbus_current, MAX_VBUS_CURRENT, color=COLOR_YELLOW, unit="mA")),
        ("VBUS Power", f"{vbus_power} W".rjust(10))
    ]
    return make_status_panel("Input Power Status", rows, style=THEME["input"], icon="🔌")

def check_shutdown_condition(voltages, current):
    return any(v < LOW_VOL for v in voltages) and current < 50

def make_layout() -> Layout:
    layout = Layout()
    layout.split_column(
        Layout(name="header", size=3),
        Layout(name="body"),
    )

    layout["body"].split_column(
        Layout(name="power", ratio=1),
        Layout(name="battery", ratio=2),
        Layout(name="cells", ratio=1)
    )

    return layout


charging_state_map = {
    0x40: ("⚡ [bold green]Fast Charging[/bold green]", "green"),
    0x80: ("🔋 [bold green]Charging[/bold green]", "green"),
    0x20: ("🔌 [bold yellow]Discharging[/bold yellow]", "yellow"),
    0x00: ("💤 [bold cyan]Idle[/bold cyan]", "cyan")
}

def get_charging_state(data_byte):
    if (data_byte & 0x40):
        return charging_state_map[0x40]
    elif(data_byte & 0x80):
        return charging_state_map[0x80]
    else:
        return charging_state_map[0x20]
    

def main():


    layout = make_layout()
    
    with Live(layout, refresh_per_second=4, screen=True) as live:
        while True:
            try:
                data = bus.read_i2c_block_data(ADDR, 0x02, 0x01)
                state_text, state_style = get_charging_state(data[0])
                cpu_tmp = get_cpu_temperature()
                timestamp = datetime.now().strftime('%Y-%m-%d %H:%M:%S')
                layout["header"].update(
                    Panel(f"Battery Monitor | {timestamp} | Status: {state_text} | cputmp:{cpu_tmp}'C", 
                          style=state_style)
                )

                data = bus.read_i2c_block_data(ADDR, 0x10, 0x06)
                layout["power"].update(make_power_panel(data))

                data = bus.read_i2c_block_data(ADDR, 0x20, 0x0C)
                battery_panel, current = make_battery_panel(data)
                layout["battery"].update(battery_panel)

                data = bus.read_i2c_block_data(ADDR, 0x30, 0x08)
                cell_panel, voltages = make_cell_voltages_panel(data)
                layout["cells"].update(cell_panel)

                if check_shutdown_condition(voltages, current):
                    low_count += 1
                    if low_count >= 3:
                        warning = Panel(
                            "[bold red]⚠️ WARNING: Low battery voltage detected! System will shutdown soon...[/bold red]",
                            border_style="red",
                            style="red"
                        )
                        layout["cells"].update(warning)
                else:
                    low_count = 0

            except Exception as e:
                error_panel = Panel(
                    f"[bold red]Error reading battery data: {str(e)}[/bold red]",
                    title="[bold red]ERROR[/bold red]",
                    border_style="red"
                )
                layout["battery"].update(error_panel)
            
            time.sleep(0.5)

if __name__ == "__main__":
    try:
        main()
    except KeyboardInterrupt:
        console.print("\n[bold yellow]Monitoring stopped by user[/bold yellow]")
    except Exception as e:
        console.print(f"[bold red]Fatal error: {str(e)}[/bold red]")
