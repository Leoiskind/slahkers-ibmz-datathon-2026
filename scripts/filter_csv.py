import sys

def filter_csv(input_path, output_path, keyword="Manchester", keep_header=True):
    with open(input_path, "r", encoding="utf-8", newline="") as infile, \
         open(output_path, "w", encoding="utf-8", newline="") as outfile:
        for i, line in enumerate(infile):
            if (keep_header and i == 0) or keyword in line:
                outfile.write(line)

if __name__ == "__main__":
    if len(sys.argv) != 3:
        print("Usage: python scripts/filter_csv.py input.csv output.csv")
        sys.exit(1)
    filter_csv(sys.argv[1], sys.argv[2])