"""Foodweb: offline food-web analyser for plain-text webs.

A web file holds one relationship per line, ``prey -> predator [@ weight]``,
plus optional ``species NAME`` declarations for species with no links yet.
Commands report trophic levels, connectance, feeding loops, food chains,
secondary extinctions, keystone species, energy paths and Graphviz output,
and ``check`` gates a web in CI. Standard library only.
"""

import argparse
import json
import math
import re
import sys
from collections import deque, namedtuple

NAME_RE = re.compile(r"^[A-Za-z0-9_][A-Za-z0-9_.-]*$")
LINK_RE = re.compile(r"^(?P<prey>\S+)\s*->\s*(?P<pred>\S+)(?:\s*@\s*(?P<weight>\S+))?$")
SPECIES_RE = re.compile(r"^species\s+(?P<name>\S+)$")

EXIT_OK = 0
EXIT_FAIL = 1
EXIT_INPUT = 2

Issue = namedtuple("Issue", "line severity message")


class Web:
    """A parsed food web: species in first-seen order, links and diet shares."""

    def __init__(self, species, links, declared=None):
        self.species = list(species)
        self.links = list(links)  # (prey, predator, raw weight)
        self.declared = dict(declared or {})
        self.eaten_by = {s: set() for s in self.species}
        self.diet = {s: {} for s in self.species}  # predator -> {prey: share}
        raw = {s: {} for s in self.species}
        for prey, pred, weight in self.links:
            raw[pred][prey] = weight
            self.eaten_by[prey].add(pred)
        for pred, prey_weights in raw.items():
            total = sum(prey_weights.values())
            self.diet[pred] = {p: w / total for p, w in prey_weights.items()}


def parse(text):
    """Parse web text. Returns (Web, issues); invalid lines are skipped."""
    issues = []
    links = []
    seen_links = {}
    declared = {}
    line_of = {}

    def error(lineno, message):
        issues.append(Issue(lineno, "error", message))

    for lineno, raw in enumerate(text.splitlines(), 1):
        line = raw.split("#", 1)[0].strip()
        if not line:
            continue
        match = SPECIES_RE.match(line)
        if match:
            name = match.group("name")
            if not NAME_RE.match(name):
                error(lineno, f"invalid species name {name!r}")
            elif name in declared:
                error(lineno, f"species {name!r} already declared on line {declared[name]}")
            else:
                declared[name] = lineno
                line_of.setdefault(name, lineno)
            continue
        match = LINK_RE.match(line)
        if not match:
            error(lineno, "expected 'prey -> predator [@ weight]' or 'species NAME'")
            continue
        prey, pred = match.group("prey"), match.group("pred")
        bad = [n for n in (prey, pred) if not NAME_RE.match(n)]
        if bad:
            error(lineno, f"invalid species name {bad[0]!r}")
            continue
        weight = 1.0
        if match.group("weight") is not None:
            text_weight = match.group("weight")
            try:
                weight = float(text_weight)
            except ValueError:
                error(lineno, f"weight must be a number, got {text_weight!r}")
                continue
            if not math.isfinite(weight) or weight <= 0:
                error(lineno, f"weight must be a positive number, got {text_weight!r}")
                continue
        if prey == pred:
            error(lineno, f"{prey!r} cannot eat itself")
            continue
        if (prey, pred) in seen_links:
            first = seen_links[(prey, pred)]
            error(lineno, f"duplicate link {prey} -> {pred} (first on line {first})")
            continue
        seen_links[(prey, pred)] = lineno
        line_of.setdefault(prey, lineno)
        line_of.setdefault(pred, lineno)
        links.append((prey, pred, weight))

    return Web(line_of.keys(), links, declared), issues


def lint(web, issues):
    """Parse issues plus graph warnings: isolated species, feeding loops, empty webs."""
    found = list(issues)
    if not web.species and not issues:
        found.append(Issue(0, "warning", "web has no species or links"))
    for name, lineno in web.declared.items():
        if not web.eaten_by[name] and not web.diet[name]:
            found.append(Issue(lineno, "warning", f"species {name!r} has no links"))
    for name in sorted(loop_species(web)):
        found.append(Issue(0, "warning", f"species {name!r} sits in a feeding loop; trophic level undefined"))
    return sorted(found, key=lambda i: (i.line, i.severity != "error", i.message))


def trophic_levels(web):
    """Trophic level per species: producers are 1, consumers 1 + diet-weighted prey level.

    Species that feed into a loop (or sit in one), and species with no links at all, get None.
    """
    pending = {s: len(web.diet[s]) for s in web.species}
    levels = {}
    queue = deque()
    for s in web.species:
        if pending[s] == 0 and web.eaten_by[s]:
            levels[s] = 1.0
            queue.append(s)
    while queue:
        prey = queue.popleft()
        for pred in sorted(web.eaten_by[prey]):
            pending[pred] -= 1
            if pending[pred] == 0:
                levels[pred] = 1 + sum(share * levels[p] for p, share in web.diet[pred].items())
                queue.append(pred)
    return {s: levels.get(s) for s in web.species}


def loop_species(web):
    """Species that can reach themselves by following predator links."""
    looped = set()
    for s in web.species:
        seen = set()
        stack = list(web.eaten_by[s])
        while stack:
            node = stack.pop()
            if node == s:
                looped.add(s)
                break
            if node in seen:
                continue
            seen.add(node)
            stack.extend(web.eaten_by[node])
    return looped


def omnivory(web, levels, consumer):
    """Diet-weighted variance of prey trophic levels; None if undefined or single-prey."""
    diet = web.diet[consumer]
    if len(diet) < 2 or any(levels[p] is None for p in diet):
        return None
    mean = sum(share * levels[p] for p, share in diet.items())
    return sum(share * (levels[p] - mean) ** 2 for p, share in diet.items())


def connectance(web):
    """Links divided by species squared, the standard web connectance."""
    n = len(web.species)
    return len(web.links) / (n * n) if n else 0.0


def secondary_extinctions(web, removed):
    """Species lost when ``removed`` goes extinct, including itself.

    A consumer dies once every one of its prey is gone; producers never die secondarily.
    """
    gone = {removed}
    queue = [removed]
    while queue:
        lost = queue.pop()
        for pred in sorted(web.eaten_by[lost]):
            if pred not in gone and set(web.diet[pred]) <= gone:
                gone.add(pred)
                queue.append(pred)
    return sorted(gone)


def keystone_ranking(web):
    """(extra extinctions, species) pairs, most damaging first."""
    rows = [(len(secondary_extinctions(web, s)) - 1, s) for s in web.species]
    rows.sort(key=lambda row: (-row[0], row[1]))
    return rows


def food_chains(web, limit=20):
    """Simple chains from producers up to top predators. Returns (chains, total_count)."""
    chains = []
    total = 0

    def walk(path):
        nonlocal total
        node = path[-1]
        options = [p for p in sorted(web.eaten_by[node]) if p not in path]
        if not options:
            total += 1
            if len(chains) < limit:
                chains.append(list(path))
            return
        for pred in options:
            path.append(pred)
            walk(path)
            path.pop()

    for base in web.species:
        if not web.diet[base] and web.eaten_by[base]:
            walk([base])
    return chains, total


def energy_path(web, source, target):
    """Shortest prey-to-predator route from source to target, or None."""
    previous = {source: None}
    queue = deque([source])
    while queue:
        node = queue.popleft()
        if node == target:
            break
        for pred in sorted(web.eaten_by[node]):
            if pred not in previous:
                previous[pred] = node
                queue.append(pred)
    if target not in previous:
        return None
    route = []
    node = target
    while node is not None:
        route.append(node)
        node = previous[node]
    return list(reversed(route))


def stats(web):
    levels = trophic_levels(web)
    defined = [v for v in levels.values() if v is not None]
    omni = [omnivory(web, levels, c) for c in web.species]
    omni = [o for o in omni if o is not None]
    n = len(web.species)
    return {
        "species": n,
        "links": len(web.links),
        "connectance": round(connectance(web), 4),
        "linkage_density": round(len(web.links) / n, 4) if n else 0.0,
        "producers": sorted(s for s in web.species if not web.diet[s] and web.eaten_by[s]),
        "top_predators": sorted(s for s in web.species if web.diet[s] and not web.eaten_by[s]),
        "levels_defined": len(defined),
        "max_trophic_level": round(max(defined), 4) if defined else None,
        "mean_trophic_level": round(sum(defined) / len(defined), 4) if defined else None,
        "mean_omnivory": round(sum(omni) / len(omni), 4) if omni else None,
        "loop_species": sorted(loop_species(web)),
    }


def to_dot(web, levels):
    lines = ["digraph foodweb {", "  rankdir=BT;", "  node [shape=box];"]
    for s in web.species:
        label = f"{s}\\nTL {levels[s]:.2f}" if levels[s] is not None else f"{s}\\nTL ?"
        style = ' style=filled fillcolor="#cde8c0"' if not web.diet[s] and web.eaten_by[s] else ""
        lines.append(f'  "{s}" [label="{label}"{style}];')
    for prey, pred, _ in sorted(web.links):
        share = web.diet[pred][prey]
        suffix = f' [label="{share:.0%}"]' if len(web.diet[pred]) > 1 else ""
        lines.append(f'  "{prey}" -> "{pred}"{suffix};')
    lines.append("}")
    return "\n".join(lines) + "\n"


def gate_failures(web, max_tl=None, min_connectance=None, max_loops=None, max_cascade=None):
    """Human-readable reasons a web fails the given CI gates; empty if it passes."""
    failures = []
    st = stats(web)
    if max_tl is not None and st["max_trophic_level"] is not None and st["max_trophic_level"] > max_tl:
        failures.append(f"max trophic level {st['max_trophic_level']} exceeds {max_tl}")
    if min_connectance is not None and st["connectance"] < min_connectance:
        failures.append(f"connectance {st['connectance']} is below {min_connectance}")
    if max_loops is not None and len(st["loop_species"]) > max_loops:
        failures.append(f"{len(st['loop_species'])} loop species exceed {max_loops}")
    if max_cascade is not None and web.species:
        worst_count, worst = keystone_ranking(web)[0]
        if worst_count + 1 > max_cascade:
            failures.append(
                f"removing {worst} causes {worst_count + 1} extinctions, above {max_cascade}"
            )
    return failures


def _read(path):
    try:
        with open(path, encoding="utf-8") as handle:
            return handle.read()
    except OSError as exc:
        print(f"foodweb: cannot read {path}: {exc.strerror}", file=sys.stderr)
        return None


def _load(path):
    text = _read(path)
    if text is None:
        return None, EXIT_INPUT
    web, issues = parse(text)
    errors = [i for i in issues if i.severity == "error"]
    if errors:
        for issue in errors:
            print(f"{path}:{issue.line}: error: {issue.message}", file=sys.stderr)
        print(f"foodweb: {len(errors)} error(s); fix them or run 'lint' for details", file=sys.stderr)
        return None, EXIT_INPUT
    return web, EXIT_OK


def _dump(data, as_json):
    if as_json:
        print(json.dumps(data, indent=2, sort_keys=True))
    return EXIT_OK


def _cmd_lint(args):
    text = _read(args.file)
    if text is None:
        return EXIT_INPUT
    web, issues = parse(text)
    found = lint(web, issues)
    for issue in found:
        where = f"{args.file}:{issue.line}" if issue.line else args.file
        print(f"{where}: {issue.severity}: {issue.message}")
    errors = sum(1 for i in found if i.severity == "error")
    warnings = len(found) - errors
    print(f"{len(found)} finding(s): {errors} error(s), {warnings} warning(s)")
    return EXIT_FAIL if errors else EXIT_OK


def _cmd_stats(args):
    web, code = _load(args.file)
    if web is None:
        return code
    st = stats(web)
    if args.json:
        return _dump(st, True)
    for key in ("species", "links", "connectance", "linkage_density", "levels_defined",
                "max_trophic_level", "mean_trophic_level", "mean_omnivory"):
        print(f"{key.replace('_', ' '):<18} {st[key]}")
    print(f"{'producers':<18} {', '.join(st['producers']) or '-'}")
    print(f"{'top predators':<18} {', '.join(st['top_predators']) or '-'}")
    print(f"{'loop species':<18} {', '.join(st['loop_species']) or '-'}")
    return EXIT_OK


def _cmd_levels(args):
    web, code = _load(args.file)
    if web is None:
        return code
    levels = trophic_levels(web)
    rows = []
    for s in sorted(web.species, key=lambda n: (levels[n] is None, levels[n] or 0, n)):
        rows.append({
            "species": s,
            "trophic_level": None if levels[s] is None else round(levels[s], 4),
            "prey": sorted(web.diet[s]),
            "predators": sorted(web.eaten_by[s]),
        })
    if args.json:
        return _dump(rows, True)
    print(f"{'species':<16}{'TL':>6}  prey -> predators")
    for row in rows:
        tl = "?" if row["trophic_level"] is None else f"{row['trophic_level']:.2f}"
        print(f"{row['species']:<16}{tl:>6}  {', '.join(row['prey']) or '-'} -> {', '.join(row['predators']) or '-'}")
    return EXIT_OK


def _cmd_chains(args):
    web, code = _load(args.file)
    if web is None:
        return code
    chains, total = food_chains(web, args.limit)
    if not chains:
        print("no food chains found")
        return EXIT_OK
    for chain in chains:
        print(" -> ".join(chain))
    if total > len(chains):
        print(f"... showing {len(chains)} of {total} chains (use --limit to see more)")
    else:
        print(f"{total} chain(s); longest has {max(len(c) for c in chains)} species")
    return EXIT_OK


def _cmd_extinct(args):
    web, code = _load(args.file)
    if web is None:
        return code
    if args.species not in web.species:
        print(f"foodweb: unknown species {args.species!r}", file=sys.stderr)
        return EXIT_INPUT
    lost = secondary_extinctions(web, args.species)
    if args.json:
        return _dump({"removed": args.species, "extinct": lost}, True)
    print(f"removing {args.species} causes {len(lost)} extinction(s):")
    for name in lost:
        print(f"  {name}")
    return EXIT_OK


def _cmd_keystone(args):
    web, code = _load(args.file)
    if web is None:
        return code
    rows = [r for r in keystone_ranking(web)[: args.top] if r[0] > 0]
    if args.json:
        return _dump([{"species": s, "extra_extinctions": n} for n, s in rows], True)
    if not rows:
        print("no species cause secondary extinctions")
        return EXIT_OK
    for extra, name in rows:
        print(f"{name:<16} {extra} secondary extinction(s)")
    return EXIT_OK


def _cmd_path(args):
    web, code = _load(args.file)
    if web is None:
        return code
    for name in (args.source, args.target):
        if name not in web.species:
            print(f"foodweb: unknown species {name!r}", file=sys.stderr)
            return EXIT_INPUT
    route = energy_path(web, args.source, args.target)
    if route is None:
        print(f"no energy path from {args.source} to {args.target}")
        return EXIT_FAIL
    print(" -> ".join(route) + f"  ({len(route) - 1} link(s))")
    return EXIT_OK


def _cmd_dot(args):
    web, code = _load(args.file)
    if web is None:
        return code
    dot = to_dot(web, trophic_levels(web))
    if args.output:
        with open(args.output, "w", encoding="utf-8") as handle:
            handle.write(dot)
        print(f"wrote {args.output}")
    else:
        sys.stdout.write(dot)
    return EXIT_OK


def _cmd_check(args):
    web, code = _load(args.file)
    if web is None:
        return code
    failures = gate_failures(
        web,
        max_tl=args.max_tl,
        min_connectance=args.min_connectance,
        max_loops=args.max_loops,
        max_cascade=args.max_cascade,
    )
    if failures:
        for reason in failures:
            print(f"FAIL {args.file}: {reason}")
        return EXIT_FAIL
    print(f"PASS {args.file}")
    return EXIT_OK


def build_parser():
    parser = argparse.ArgumentParser(prog="foodweb", description="Offline food-web analyser.")
    sub = parser.add_subparsers(dest="command", required=True)

    p = sub.add_parser("lint", help="check a web file with line-numbered findings")
    p.add_argument("file")
    p.set_defaults(func=_cmd_lint)

    p = sub.add_parser("stats", help="connectance, levels, omnivory and loops")
    p.add_argument("file")
    p.add_argument("--json", action="store_true")
    p.set_defaults(func=_cmd_stats)

    p = sub.add_parser("levels", help="trophic level of every species")
    p.add_argument("file")
    p.add_argument("--json", action="store_true")
    p.set_defaults(func=_cmd_levels)

    p = sub.add_parser("chains", help="list food chains from producers to top predators")
    p.add_argument("file")
    p.add_argument("--limit", type=int, default=20)
    p.set_defaults(func=_cmd_chains)

    p = sub.add_parser("extinct", help="secondary extinctions after removing one species")
    p.add_argument("file")
    p.add_argument("species")
    p.add_argument("--json", action="store_true")
    p.set_defaults(func=_cmd_extinct)

    p = sub.add_parser("keystone", help="rank species by the extinctions their loss causes")
    p.add_argument("file")
    p.add_argument("--top", type=int, default=5)
    p.add_argument("--json", action="store_true")
    p.set_defaults(func=_cmd_keystone)

    p = sub.add_parser("path", help="shortest energy route from one species to another")
    p.add_argument("file")
    p.add_argument("source")
    p.add_argument("target")
    p.set_defaults(func=_cmd_path)

    p = sub.add_parser("dot", help="write a Graphviz digraph")
    p.add_argument("file")
    p.add_argument("-o", "--output")
    p.set_defaults(func=_cmd_dot)

    p = sub.add_parser("check", help="CI gates; exit 1 when a limit is broken")
    p.add_argument("file")
    p.add_argument("--max-tl", type=float, help="largest allowed trophic level")
    p.add_argument("--min-connectance", type=float, help="smallest allowed connectance")
    p.add_argument("--max-loops", type=int, help="most species allowed in feeding loops")
    p.add_argument("--max-cascade", type=int, help="most extinctions one removal may cause")
    p.set_defaults(func=_cmd_check)

    return parser


def main(argv=None):
    args = build_parser().parse_args(argv)
    return args.func(args)


if __name__ == "__main__":
    sys.exit(main())
