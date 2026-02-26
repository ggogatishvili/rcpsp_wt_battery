import random
import os
import csv
from datetime import datetime
import networkx as nx

def read_input(filename):
    with open(filename, 'r') as f:
        lines = [line.strip() for line in f if line.strip()]

    # First line: number of tasks and resources
    n, m = map(int, lines[0].split())

    # Second line: resource capacities
    capacities = list(map(int, lines[1].split()))

    # Next n lines: task info
    tasks = []
    for i in range(n):
        parts = list(map(int, lines[i+2].split()))
        p_i = parts[0]  # task duration
        r_i = parts[1:m + 1]  # resource requirements
        num_succ = parts[m + 1]  # number of successors
        successors = parts[m + 2:m + 2 + num_succ] if num_succ > 0 else []

        task = {
            "id": i,
            "duration": p_i,
            "resources": r_i,
            "num_successors": num_succ,
            "successors": successors
        }
        tasks.append(task)

    # Last line: floating-point prices c_1 ... c_h
    prices  = list(map(float, lines[2 + n].split()))

    return {
        "n": n,
        "m": m,
        "capacities": capacities,
        "tasks": tasks,
        "prices": prices
    }

def load_prices_from_first_monday(csv_path):
    prices = []
    daily_prices = []
    current_day = None
    first_date = None

    with open(csv_path, newline='', encoding='utf-8') as f:
        reader = csv.DictReader(f)

        for row in reader:
            date = datetime.strptime(row["day"], "%d/%m/%Y")
            cost_str = row["cost"].strip()

            # Store None if missing
            cost = float(cost_str) if cost_str != "" else None

            if first_date is None:
                first_date = date

            if current_day is None:
                current_day = date

            # New day detected
            if date != current_day:

                # Fix time change
                if len(daily_prices) == 23:
                    daily_prices.insert(2, daily_prices[1])
                elif len(daily_prices) == 25:
                    del daily_prices[2]

                prices.extend(daily_prices)
                daily_prices = []
                current_day = date

            daily_prices.append(cost)

        # Fix time change for the last day
        if len(daily_prices) == 23:
            daily_prices.insert(2, daily_prices[1])
        elif len(daily_prices) == 25:
            del daily_prices[2]

        prices.extend(daily_prices)

    # Fill missing values using previous and next day same hour
    total_hours = len(prices)

    for i in range(total_hours):
        if prices[i] is None:
            prev_i = i - 24
            next_i = i + 24

            prev_val = prices[prev_i] if prev_i >= 0 else None
            next_val = prices[next_i] if next_i < total_hours else None

            if prev_val is not None and next_val is not None:
                prices[i] = (prev_val + next_val) / 2
            elif prev_val is not None:
                prices[i] = prev_val
            elif next_val is not None:
                prices[i] = next_val

    # Find first Monday
    days_until_monday = (7 - first_date.weekday()) % 7
    start_index = days_until_monday * 24

    return prices[start_index:]


def add_task_dates_and_late_prices(data):
    h = len(data["prices"])
    tasks = data["tasks"]
    n = data["n"]

    # Build dependency graph
    G = nx.DiGraph()
    for t in tasks:
        G.add_node(t["id"], duration=t["duration"])
        for succ in t["successors"]:
            G.add_edge(t["id"], succ)

    # Compute topological order and longest paths
    topo_order = list(nx.topological_sort(G))
    longest_path_len = {t["id"]: 0 for t in tasks}
    for t_id in topo_order:
        preds = list(G.predecessors(t_id))
        if preds:
            longest_path_len[t_id] = max(longest_path_len[p] + tasks[p]["duration"] for p in preds)
        else:
            longest_path_len[t_id] = 0

    price_average = sum(data["prices"]) / h

    for t in tasks:
        depth = longest_path_len[t["id"]]
        dur = t["duration"]

        # Release date - earliest start based on processing times of predecessors not considering resource constraints
        release_date = depth

        # Due date - release date + duration + some percentage of remaining horizon
        base_due = release_date + dur
        lax_slack = random.randint(int(0.1 * (h - base_due)), int(0.2 * (h - base_due)))
        due_date = base_due + lax_slack
        due_date = min(due_date, h - 1)

        t["release_date"] = release_date
        t["due_date"] = due_date
        t["late_price"] = round(price_average / 10 * random.uniform(0.5, 1), 2)  # small variation

def get_random_monday_block(prices_from_first_monday, horizon):
    total_length = len(prices_from_first_monday)

    # Number of valid Mondays
    num_mondays = (total_length - horizon) // (7 * 24)

    if num_mondays < 0:
        raise ValueError("Horizon larger than available data.")

    random_week = random.randint(0, num_mondays)

    start_index = random_week * 7 * 24

    return prices_from_first_monday[start_index:start_index + horizon]

def write_output(data, filename="output.txt"):
    with open(filename, "w") as f:
        f.write(f"{data['n']} {data['m']}\n")
        f.write(" ".join(map(str, data["capacities"])) + "\n")

        for t in data["tasks"]:
            line = [t["duration"]] + t["resources"] + [t["num_successors"]] + t["successors"] + \
                   [t["release_date"], t["due_date"], t["late_price"]]
            f.write(" ".join(map(str, line)) + "\n")


        f.write(" ".join(map(str, data["prices"])) + "\n")

if __name__ == "__main__":
    input_dir = "instances_original"
    output_dir = "../instances_new"

    os.makedirs(output_dir, exist_ok=True)  # Make sure output directory exists

    costs_file = "electricity_cost_eur_mwh_2025.csv"
    costs = load_prices_from_first_monday(costs_file)

    for filename in os.listdir(input_dir):
        if filename.endswith(".txt"):
            input_path = os.path.join(input_dir, filename)
            output_path = os.path.join(output_dir, filename)

            data = read_input(input_path)
            add_task_dates_and_late_prices(data)
            data["prices"] = get_random_monday_block(costs, len(data["prices"]))
            write_output(data, output_path)

            print(f"Processed {filename}")