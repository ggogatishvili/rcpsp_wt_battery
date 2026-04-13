import random

def generate_irace_instances():
    num_sizes = 20
    num_instances = 20
    directory = "instances_tuning"
    output_filename = "instances-list-new.txt"

    # Create 20 lists of 20 instances and shuffle instances in each list to ensure random order of instances for each size
    index_lists = []
    for _ in range(num_sizes):
        indices = list(range(1, num_instances + 1))
        random.shuffle(indices)
        index_lists.append(indices)

    # Group into blocks of 10 (matches the setting in scenario) evenly spaced out sizes
    with open(output_filename, 'w') as f:
        for i in range(num_instances):

            # First block: Odd sizes (1, 3, 5... 19)
            for size in range(1, num_sizes + 1, 2):
                size_idx = size - 1
                instance_index = index_lists[size_idx][i]
                filepath = f"{directory}/{size}_{instance_index}.txt"
                f.write(filepath + "\n")

            # Second block: Even sizes (2, 4, 6... 20)
            for size in range(2, num_sizes + 1, 2):
                size_idx = size - 1
                instance_index = index_lists[size_idx][i]
                filepath = f"{directory}/{size}_{instance_index}.txt"
                f.write(filepath + "\n")

if __name__ == "__main__":
    generate_irace_instances()