import pandas as pd
import numpy as np

# Hardcoded paths
in_filepath = r"C:/TestFolder/AEBusCommandsLog.csv"
out_filepath = r"C:/TestFolder/output.csv"

# Set to an integer like 1000 to limit the rows used for stats,
# or keep None to use all rows.
MAX_SAMPLES_FOR_STATS = 1


def parse_raw_csv(in_file, out_file, max_samples=None):
    df = pd.read_csv(in_file)

    # Clean numeric fields
    df['dt last(us)'] = pd.to_numeric(df['dt last(us)'].astype(str).str.rstrip('s'), errors='coerce')
    df['Data'] = pd.to_numeric(df['Data'], errors='coerce')

    # Pair each B->A command transition with the next A->B latency event in sequence.
    prev_input = df['Input'].shift(1)
    b_to_a_idx = df.index[((prev_input == 'B') & (df['Input'] == 'A')) | ((prev_input.isna()) & (df['Input'] == 'A'))]
    a_to_b_idx = df.index[(prev_input == 'A') & (df['Input'] == 'B')]

    if len(a_to_b_idx) == 0 or len(b_to_a_idx) == 0:
        empty = pd.DataFrame(columns=['command', 'lat (ms)'])
        empty.to_csv(out_file, index=False)
        print(f"Saved 0 rows into intermediate file: {out_file}")
        stats_df = analyze_command_latency(out_file)
        stats_file = out_file.replace('.csv', '_stats.csv')
        stats_df.to_csv(stats_file, index=False)
        print(f"Stats CSV saved to {stats_file}")
        return stats_df

    next_latency_positions = []
    j = 0
    for ba in b_to_a_idx:
        while j < len(a_to_b_idx) and a_to_b_idx[j] <= ba:
            j += 1
        if j < len(a_to_b_idx):
            next_latency_positions.append(j)
        else:
            next_latency_positions.append(None)

    valid = [p is not None and (b_to_a_idx[i] + 2 < len(df)) and (b_to_a_idx[i] + 3 < len(df))
             for i, p in enumerate(next_latency_positions)]

    transition_indices = b_to_a_idx[valid]
    latency_indices = pd.Index([a_to_b_idx[p] for p in np.array(next_latency_positions, dtype=object)[valid]])

    transitions = df.loc[transition_indices].copy()
    transitions['command_raw'] = df.loc[transition_indices + 2, 'Data'].to_numpy()
    transitions['subcommand_raw'] = df.loc[transition_indices + 3, 'Data'].to_numpy()
    transitions['lat (ms)'] = df.loc[latency_indices, 'dt last(us)'].to_numpy() * 1000.0

    def format_command(row):
        cmd = row['command_raw']
        sub = row['subcommand_raw']

        if pd.isna(cmd):
            return np.nan

        cmd_int = int(cmd)

        # Only these command IDs need command-subcommand naming.
        # Everything else stays as its plain command number.
        if cmd_int in [71, 118, 204, 248] and pd.notna(sub):
            return f"{cmd_int}-{int(sub)}"

        return str(cmd_int)

    transitions['command'] = transitions.apply(format_command, axis=1)

    output = transitions[['command', 'lat (ms)']].dropna()

    # Keep up to max_samples rows per command when a limit is set.
    # If a command appears fewer than max_samples times, all of its rows are kept.
    if max_samples is not None:
        output = output.groupby('command', group_keys=False).head(max_samples)

    output.to_csv(out_file, index=False)
    print(f"Saved {len(output)} rows into intermediate file: {out_file}")

    stats_df = analyze_command_latency(out_file)
    stats_file = out_file.replace('.csv', '_stats.csv')
    stats_df.to_csv(stats_file, index=False)
    print(f"Stats CSV saved to {stats_file}")
    return stats_df


def analyze_command_latency(csv_file):
    df = pd.read_csv(csv_file)

    stats = df.groupby('command')['lat (ms)'].agg([
        ('min', 'min'),
        ('max', 'max'),
        ('mean', 'mean'),
        ('std', 'std'),
        ('count', 'count')
    ]).reset_index()

    stats.columns = ['command', 'best_case_ms', 'worst_case_ms', 'average_ms', 'std_dev_ms', 'samples']

    def command_sort_key(value):
        if isinstance(value, str) and '-' in value:
            base, sub = value.split('-', 1)
            return (int(base), int(sub))
        return (int(value), 0)

    stats = stats.sort_values('command', key=lambda s: s.map(command_sort_key)).reset_index(drop=True)
    return stats.round(6)


if __name__ == "__main__":
    parse_raw_csv(in_filepath, out_filepath, max_samples=MAX_SAMPLES_FOR_STATS)
