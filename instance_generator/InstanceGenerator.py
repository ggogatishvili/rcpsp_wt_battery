import pprint
import random
import os
import networkx as nx
import matplotlib.pyplot as plt

def read_input(filename):
    with open(filename, 'r') as f:
        lines = [line.strip() for line in f if line.strip()]

    # --- First line: number of tasks and resources ---
    n, m = map(int, lines[0].split())

    # --- Second line: resource capacities ---
    capacities = list(map(int, lines[1].split()))

    # --- Next n lines: task info ---
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

    # --- Last line: floating-point prices c_1 ... c_h ---
    prices  = list(map(float, lines[2 + n].split()))

    return {
        "n": n,
        "m": m,
        "capacities": capacities,
        "tasks": tasks,
        "prices": prices
    }


def add_task_dates_and_late_prices(data):
    h = len(data["prices"])
    tasks = data["tasks"]
    n = data["n"]

    # --- Build dependency graph ---
    G = nx.DiGraph()
    for t in tasks:
        G.add_node(t["id"], duration=t["duration"])
        for succ in t["successors"]:
            G.add_edge(t["id"], succ)

    # --- Compute topological order and longest paths ---
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


def write_output(data, filename="output.txt"):
    with open(filename, "w") as f:
        f.write(f"{data['n']} {data['m']}\n")
        f.write(" ".join(map(str, data["capacities"])) + "\n")

        for t in data["tasks"]:
            line = [t["duration"]] + t["resources"] + [t["num_successors"]] + t["successors"] + \
                   [t["release_date"], t["due_date"], t["late_price"]]
            f.write(" ".join(map(str, line)) + "\n")


        f.write(" ".join(map(str, data["prices"])) + "\n")


def display_task_graph(tasks):
    G = nx.DiGraph()

    # Add nodes and edges
    for t in tasks:
        G.add_node(t["id"], label=f"Task {t['id']}\nDur: {t['duration']}")
        for succ in t["successors"]:
            G.add_edge(t["id"], succ)

    # Layout and drawing
    pos = nx.spring_layout(G, seed=42)
    labels = {t["id"]: f"{t['id']}" for t in tasks}

    plt.figure(figsize=(10, 6))
    nx.draw(G, pos, with_labels=True, labels=labels,
            node_color="lightblue", node_size=800,
            font_size=10, arrowsize=20, font_weight="bold")
    plt.title("Task Graph (Successor Relationships)")
    plt.show()


def display_price_graph(prices):
    plt.figure(figsize=(8, 5))
    plt.plot(range(1, len(prices) + 1), prices, marker='o', linestyle='-', linewidth=2)
    plt.title("Prices Over Time")
    plt.xlabel("Time (index)")
    plt.ylabel("Price")
    plt.grid(True)
    plt.tight_layout()
    plt.show()


if __name__ == "__main__":
    input_dir = "instances_original"
    output_dir = "../instances_new"

    os.makedirs(output_dir, exist_ok=True)  # Make sure output directory exists

    for filename in os.listdir(input_dir):
        if filename.endswith(".txt"):
            input_path = os.path.join(input_dir, filename)
            output_path = os.path.join(output_dir, filename)

            data = read_input(input_path)
            add_task_dates_and_late_prices(data)
            write_output(data, output_path)

            # if (filename == "1_1.txt"):
            #     display_task_graph(data["tasks"])
            #     display_price_graph(data["prices"])

            print(f"Processed {filename}")