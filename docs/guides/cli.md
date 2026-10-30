# Command line

The `disscube` command works on a [pipeline file](pipeline_files.md). Five subcommands:

| Command | What it does | Network |
|---|---|---|
| `disscube validate FILE` | checks the pipeline file and prints the plan; nothing is fetched | no |
| `disscube fetch FILE` | downloads the remote `file` sources of the pipeline and checks their SHA-256 | yes |
| `disscube run FILE` | fetches the sources, derives the variables and, with `--output`, exports them | yes |
| `disscube export FILE -o OUT` | exports variables from a workspace that already has them | no |
| `disscube plot TIF` | draws the bands of an exported GeoTIFF in a PNG | no |

## `validate`

```bash
disscube validate sources.toml          # the plan, or the first error
disscube validate sources.toml --json   # the same as JSON
```

An unknown operator, a `percentage` without `class_code`, a source that does not exist or a parameter the operator does
not know fail here, before any download.

## `fetch`

Downloads the `file` sources that declare a `url` (and a `sha256`) into the workspace cache, and verifies them. It is
the step to run once before working offline.

## `run`

```bash
disscube run sources.toml --workspace data/sources --output data/sources.tif
```

| Option | Meaning |
|---|---|
| `--workspace DIR` | where the run keeps its files; default: the `workspace` of the file, otherwise a folder named after the file |
| `--output`, `-o FILE` | export the derived variables: GeoTIFF, or netCDF if the name ends in `.nc` |
| `--dry-run` | simulate the plan without downloading or computing |
| `--json` | print the execution report as JSON |
| `-v`, `--verbose` | log each step |

The workspace receives `catalog.db`, `store/`, `raw/` and `run.json` (see
[what a run leaves behind](pipeline_files.md#what-a-run-leaves-behind)). The exported GeoTIFF has one band per variable
and per year, named after the variable; `-o` with an existing workspace redoes only what changed.

## `export`

```bash
disscube export sources.toml -o data/cellspace.tif --workspace data/sources --variables l1_class
```

Exports from an existing workspace without deriving anything. `--variables` selects some of the derived variables
(default: all); `--workspace` defaults to `data/cube` or the workspace of the file. The same output is made by
`run --output`.

## `plot`

```bash
pip install 'disscube[plot]'            # needs matplotlib
disscube plot data/sources.tif -o look.png --cols 2 --crop
```

A quick look, not a publication figure: one panel per band; the scale follows the values (shares in [0, 1] as 0 to
100 %, a few integer codes as classes, otherwise continuous between the 2nd and 98th percentiles); cells without data
are white.

| Option | Meaning |
|---|---|
| `--output`, `-o FILE` | PNG; default: the GeoTIFF's name with `.png` |
| `--bands NAME …` | only these bands or variables |
| `--year N` | only the bands of this year |
| `--cols N` | panels per row |
| `--shared-colorbar` | one colour bar for all panels, when all are shares |
| `--crop` | trim the empty margin (the bounding box of the cells with data) |
| `--dpi N` | resolution of the PNG (default 150) |

## Exit status

`0` when the command finished; `2` for an error of the pipeline file, a missing optional extra or an unreadable
file, with the message on the standard error (as JSON when `--json` is given).
