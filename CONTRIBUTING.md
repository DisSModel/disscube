# Contributing to DisSCube

Thank you for your interest in contributing to DisSCube! We welcome contributions from research group members, students, and the broader open-source community.

---

## Contribution Workflow

We follow a **Trunk-Based Development** model: all active development targets the `main` branch through short-lived branches and Pull Requests.

### 1. Issues First
Before writing code or opening a pull request, make sure an issue tracks the task:
- Navigate to the **Issues** tab and click **New issue**.
- Choose the relevant template (**Feature Task**, **Bug Report**, **Documentation**, or **Onboarding**).
- Provide the required details. Relevant labels will be attached automatically.

### 2. Creating Your Branch

#### For Lab Members & Collaborators (Direct Access)
1. Open the assigned issue on GitHub.
2. In the right sidebar under **Development**, click **"Create a branch"**.
3. Use conventional prefixes: `feat/<issue-id>-short-desc`, `fix/<issue-id>-short-desc`, `docs/<issue-id>-short-desc`, or `chore/<issue-id>-short-desc`.
4. Pull and checkout the branch locally:
   ```bash
   git fetch origin
   git checkout <branch-name>
   ```

#### For External Contributors (Fork Workflow)
1. Fork the repository to your personal GitHub account.
2. Clone your fork locally:
   ```bash
   git clone https://github.com/<your-username>/disscube.git
   cd disscube
   ```
3. Create a descriptive feature branch targeting `main`:
   ```bash
   git checkout -b feat/my-improvement
   ```

---

### 3. Submitting a Pull Request (PR)

1. Verify that the tests and the linter pass locally:
   ```bash
   pytest tests/
   ruff check .
   mypy disscube
   ```
2. Add an entry to the `[Unreleased]` section of [`CHANGELOG.md`](CHANGELOG.md) for any user-visible change.
3. Push your branch to GitHub:
   - **Lab members:** `git push -u origin <branch-name>`
   - **External contributors:** `git push -u origin feat/my-improvement` (to your fork)
4. Open a Pull Request targeting `DisSModel/disscube:main`.
5. Complete the checklist provided by the Pull Request template.
6. Ensure the PR description explicitly links the issue it resolves (e.g., `Closes #15`).
7. A maintainer will review your submission. Once approved, the changes will be integrated via **Squash and merge**, and the working branch will be automatically deleted.

---

## Development Setup

### 1. Clone Repository

```bash
git clone https://github.com/DisSModel/disscube.git
cd disscube
```

### 2. Virtual Environment Setup

```bash
python -m venv venv
source venv/bin/activate  # On Windows: venv\Scripts\activate
```

### 3. Install Development Dependencies

```bash
pip install -e ".[dev]"
```

Tests that need optional packages are skipped, not failed, when the package is missing. To run all of them, install the extras too:

```bash
pip install -e ".[dev,bdc,netcdf,dissmodel]"
```

### 4. Running the Test Suite

```bash
pytest tests/
```

### 5. Linting

```bash
ruff check .
```

CI pins the `ruff` minor version (see the `dev` extra in `pyproject.toml`); use the same one locally.

### 6. Type checking

```bash
mypy disscube
```

Third-party packages without type stubs are listed by name in `[tool.mypy]` in `pyproject.toml`; do not add a blanket `--ignore-missing-imports`.

---

## Coding Standards

* **PEP 8 Compliance:** Follow standard Python formatting conventions.
* **Type Annotations:** Provide type hints for public functions, methods, and class attributes.
* **Docstrings:** Document new modules, classes, and functions using NumPy docstring style.
* **Tests:** Add or update automated tests covering new behavior.
* **New operators:** Operators register themselves; see `docs/architecture/operators.md` for the `compute()` contract.

---

## Troubleshooting: Accidentally Committed to `main`?

If you committed directly to your local `main` branch and your push was blocked by repository rules, migrate your changes to a feature branch without losing work:

```bash
# 1. Create a new branch preserving your unpushed commits
git branch feat/<issue-id>-my-task

# 2. Reset your local main back to the clean remote state
git reset --hard origin/main

# 3. Switch to your new branch and push
git checkout feat/<issue-id>-my-task
git push -u origin feat/<issue-id>-my-task
```

---

## License

By contributing to DisSCube, you agree that your contributions will be licensed under the project's [MIT License](LICENSE).
