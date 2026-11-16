"""Standalone script to simplify transfer of RAIE controller parameters between config files
Created by Zane Miller, 2025/06/04
Ver 1.0: Initial release
Ver 1.1: Docstring cleanup, add default args for __init__(), only use YAML if specified"""
import csv
import os
import re
import argparse

class ParamPorter():
    """Container class for param_porter operations.
    Property "enabled": set of RAIE modules in use, None = enable all."""
    def __init__(self, enable=(), disable=(), enableall=False):
        if enableall:
            self.enabled = None
        else:
            self.enabled = set(["Limit Loops", "Memory Mode", "PID RE", "PID LE", "PID TE", "PID FE", "PID RE_SE", "PID FE_SE"])
            for module in enable:
                self.enabled.add(module)
            for module in disable:
                self.enabled.discard(module)
        self.fieldsdict = {}

    def read_csv(self, csvin):
        """Read params from input CSV file"""
        with open(csvin, newline='', encoding='utf8') as fields:
            freader = csv.reader(fields, delimiter=',')
            enmod = True
            for row in freader:
                if row[0][:2] == "**":
                    enmod = True if self.enabled is None else row[0][2:-2] in self.enabled
                elif enmod:
                    self.fieldsdict[row[0]] = row[1]

    def write_csv(self, csvin, csvout):
        """Write loaded params to output CSV file using same format as input"""
        if csvin == csvout:
            csvout = csvin.removesuffix('.csv')+"_temp.csv"
            print("Input & output CSV location identical. Overwriting input CSV.")
            overwrite = True
        else:
            overwrite = False
        with open(csvin, newline='', encoding='utf8') as fieldsr,\
          open(csvout, 'w', newline='', encoding='utf8') as fieldsw:
            freader = csv.reader(fieldsr, delimiter=',')
            fwriter = csv.writer(fieldsw, delimiter=',', lineterminator='\n')
            enmod = True
            for row in freader:
                if row[0][:2] == "**":
                    enmod = True if self.enabled is None else row[0][2:-2] in self.enabled
                    fwriter.writerow(row)
                elif enmod and row[0] in self.fieldsdict:
                    fwriter.writerow([row[0],self.fieldsdict[row[0]]])
                else:
                    fwriter.writerow([row[0],None])
        if overwrite:  # Overwrite input CSV with output CSV
            os.remove(csvin)
            os.rename(csvout, csvin)

    def read_yaml(self, yamlread):
        """Read YAML format config file and extract RAIE parameter values."""
        with open(yamlread, encoding='utf8') as yrfile:
            rtext = yrfile.read()
        for field in self.fieldsdict:
            self.fieldsdict[field] = rtext.split(f'"{field}=',1)[1].split(';"',1)[0]

    def write_yaml(self, yamlwrite, yamltemp):
        """Write loaded params into a YAML file, replacing values in the specified template."""
        with open(yamltemp, encoding='utf8') as ytfile:
            ttext = ytfile.read()
        for field, value in self.fieldsdict.items():
            if value:  # skip blank entries
                ttext = re.sub(f'(?<={field}=).*?(?=;")', value, ttext)
        with open(yamlwrite, 'w', encoding='utf8', newline='\n') as ywfile:
            ywfile.write(ttext)

if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Standalone script to simplify transfer of RAIE controller parameters between config files.",
        epilog="Default enabled modules: [Limit Loops, Memory Mode, PID RE, PID LE, PID TE, PID FE, PID RE_SE, PID FE_SE]")
    parser.add_argument('csvin', help="Path to input CSV file.")
    parser.add_argument('csvout', help="Path to output CSV file. Overwrite if same as csvin.")
    parser.add_argument('-yr', '--yamlread', help="Path to YAML file to read parameters from, taking precedence over values in csvin.")
    parser.add_argument('-yw', '--yamlwrite', help="Path to YAML file to write parameters to.")
    parser.add_argument('-yt', '--yamltemplate', help="Path to YAML file to use as template. Default is YAMLWRITE (will overwrite).")
    group = parser.add_mutually_exclusive_group()
    group.add_argument('-e', '--enable', nargs='*', default=[], help="RAIE modules to enable processing of. Must match csvin.")
    parser.add_argument('-d', '--disable', nargs='*', default=[], help="RAIE modules to disable processing of.")
    group.add_argument('-eall', '--enableall', action='store_true', help="Enable all modules specified in csvin.")
    args = parser.parse_args()
    if args.disable is not None and args.enableall:
        raise NotImplementedError("--disable not yet supported with --enableall")
    if args.yamltemplate is None:
        yamltemplate = args.yamlwrite
    else:
        yamltemplate = args.yamltemplate
    pp = ParamPorter(args.enable, args.disable, args.enableall)
    pp.read_csv(args.csvin)
    if args.yamlread is not None:
        pp.read_yaml(args.yamlread)
        pp.write_csv(args.csvin, args.csvout)  # no point in automatic write if no changes made
    if args.yamlwrite is not None:
        pp.write_yaml(args.yamlwrite,yamltemplate)
