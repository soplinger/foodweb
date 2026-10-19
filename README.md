# Foodweb

**An offline food-web analyser for plain-text webs: trophic levels, connectance, feeding loops, food chains, secondary extinctions and CI gates.**

Foodweb reads a food web from a plain-text file, one feeding link per line, and tells you how the web is built. It works out each species' trophic level, the connectance and omnivory of the web, which species sit in feeding loops, every food chain from producers to top predators, what a single extinction would take down with it, and the shortest route energy takes between two species. Errors come back with file and line numbers, and `check` fails a CI job when a web breaks your limits.

It uses only the Python 3 standard library. There are no servers, accounts, API keys or network calls.

## Features

- **Plain-text web format**: `prey -> predator [@ weight]`, with `#` comments and `species NAME` declarations for unlinked species.
- **Diet weights**: `@ weight` gives relative diet shares, normalised per predator, so `perch -> pike @ 3` plus `perch_fry -> pike @ 1` feeds pike 75% perch and 25% perch fry.
- **Line-numbered errors**: bad syntax, self-feeding, duplicate links, invalid names, non-positive weights and duplicate declarations all report `file:line`.
- **Lint with warnings**: errors fail the lint; isolated species and feeding loops are reported as warnings.
- **Trophic levels**: producers are level 1; consumers are 1 plus the diet-weighted level of their prey. Species caught in a feeding loop report `?` rather than a made-up number.
- **Web statistics**: species, links, connectance, linkage density, producers, top predators, max and mean trophic level, and mean omnivory.
- **Omnivory**: the diet-weighted variance of each consumer's prey levels, averaged over the web.
- **Food chains**: every simple chain from a producer up to a top predator, with a `--limit` and a total count.
- **Secondary extinctions**: remove a species and see every consumer that loses all of its prey, in turn.
- **Keystone ranking**: species ranked by how many extra extinctions their loss causes.
- **Energy path**: the shortest prey-to-predator route between any two species.
- **Graphviz export**: a `digraph` with trophic levels on the nodes, diet shares on the edges, and producers shaded.
- **JSON output** on `stats`, `levels`, `extinct` and `keystone`, for scripts.
- **CI gates**: `check` exits 1 when the max trophic level, minimum connectance, feeding-loop size or worst cascade goes past your limit.

## Quick start

The repository includes `examples/lake.web`, a small temperate lake:

```text
algae -> zooplankton @ 3
algae -> snail
algae -> midge_larva @ 1
detritus -> midge_larva @ 2
zooplankton -> perch_fry @ 2
midge_larva -> perch_fry @ 1
perch_fry -> perch @ 1
midge_larva -> perch @ 1
snail -> perch
perch -> pike @ 3
perch_fry -> pike @ 1
species otter        # declared but not yet linked into the web
```

Lint it, which flags the unlinked otter:

```console
$ python3 foodweb.py lint examples/lake.web
examples/lake.web:15: warning: species 'otter' has no links
1 finding(s): 0 error(s), 1 warning(s)
```

Print each species' trophic level and its predators:

```console
$ python3 foodweb.py levels examples/lake.web
species             TL  prey -> predators
algae             1.00  - -> midge_larva, snail, zooplankton
detritus          1.00  - -> midge_larva
midge_larva       2.00  algae, detritus -> perch, perch_fry
snail             2.00  algae -> perch
zooplankton       2.00  algae -> perch_fry
perch_fry         3.00  midge_larva, zooplankton -> perch, pike
perch             3.33  midge_larva, perch_fry, snail -> pike
pike              4.25  perch, perch_fry -> -
otter                ?  - -> -
```

Find the shortest energy route from algae to pike:

```console
$ python3 foodweb.py path examples/lake.web algae pike
algae -> midge_larva -> perch -> pike  (3 link(s))
```

Fail a CI job when the web is too deep (pike sits at 4.25, so a limit of 4 would fail):

```console
$ python3 foodweb.py check examples/lake.web --max-tl 5
PASS examples/lake.web
```

## How to run

Use Python 3.8 or newer. Run commands from the project directory:

| Command | What it does |
| --- | --- |
| `python3 foodweb.py lint FILE` | Line-numbered errors and warnings; exit 1 on errors |
| `python3 foodweb.py stats FILE [--json]` | Connectance, levels, omnivory and loops |
| `python3 foodweb.py levels FILE [--json]` | Trophic level, prey and predators for each species |
| `python3 foodweb.py chains FILE [--limit N]` | Food chains from producers to top predators |
| `python3 foodweb.py extinct FILE SPECIES [--json]` | Secondary extinctions after one species is removed |
| `python3 foodweb.py keystone FILE [--top N] [--json]` | Species ranked by the extinctions their loss causes |
| `python3 foodweb.py path FILE SOURCE TARGET` | Shortest energy route between two species |
| `python3 foodweb.py dot FILE [-o OUT.dot]` | Graphviz digraph, to stdout or a file |
| `python3 foodweb.py check FILE [--max-tl X] [--min-connectance X] [--max-loops N] [--max-cascade N]` | CI gates; exit 1 if a limit is broken |

Exit codes: `0` success, `1` lint errors or a failed gate or missing path, `2` bad input (unreadable file, parse errors, unknown species).

Graphviz output can be rendered with `dot -Tsvg web.dot -o web.svg` if you have Graphviz installed.

### Web file format

```text
# comments run to end of line
species NAME              # declare a species with no links yet
prey -> predator          # equal diet share
prey -> predator @ 2.5    # relative weight; shares are normalised per predator
```

Names may use letters, digits, `_`, `.` and `-`, and must not start with `-` or `.`.

## How to test

Install pytest if you don't have it, then run the suite from the project directory:

```console
$ python3 -m pip install pytest
$ python3 -m pytest -q
```

The tests live in `tests/test_foodweb.py`. They cover parsing and line-numbered errors, lint warnings, trophic levels (including weighted diets and feeding loops), connectance, omnivory, secondary extinctions, keystone ranking, food chains, energy paths, Graphviz output, CI gates, JSON output and exit codes, using both small webs and `examples/lake.web`.

## Project layout

```text
foodweb.py                 the analyser and command-line interface
examples/lake.web          a small lake food web to try the commands on
tests/test_foodweb.py      pytest suite
```
