#!/usr/bin/env python3
"""
RecipeBox — a local Recipe Manager desktop app (Tkinter + SQLite).

Save this file as `recipe_box.py` and run with Python 3.8+:
    python recipe_box.py

Features:
- Add / Edit / Delete recipes
- Ingredients stored as JSON list, instructions as text, tags as comma-separated
- Search by title, tag, or ingredient
- CSV import/export
- Scale servings (text helper)
- SQLite persistence in 'recipes.db'
- Logging to 'recipebox.log'
"""

from __future__ import annotations

import csv
import json
import logging
import sqlite3
import sys
import datetime as dt
from dataclasses import dataclass
from pathlib import Path
from typing import List, Optional, Iterable, Tuple

import tkinter as tk
from tkinter import ttk, filedialog, messagebox

# ---------------------- Config & Logging ---------------------- #
APP_NAME = "RecipeBox"
DB_FILE = Path(__file__).with_name("recipes.db")
LOG_FILE = Path(__file__).with_name("recipebox.log")

logging.basicConfig(
    filename=LOG_FILE,
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
)
logger = logging.getLogger(APP_NAME)


# -------------------------- Domain Model ---------------------- #
@dataclass
class Recipe:
    id: Optional[int]
    title: str
    ingredients: List[str]  # each line an ingredient, free form
    instructions: str
    tags: List[str]         # simple tag list
    servings: int
    created_at: str

    def to_db_tuple(self) -> Tuple:
        return (
            self.title,
            json.dumps(self.ingredients, ensure_ascii=False),
            self.instructions,
            ",".join(self.tags),
            int(self.servings),
            self.created_at,
        )


# ------------------------- Repository ------------------------- #
class RecipeRepo:
    def __init__(self, db_path: Path = DB_FILE):
        self.db_path = db_path
        self.conn = sqlite3.connect(str(self.db_path))
        self.conn.row_factory = sqlite3.Row
        self._ensure_schema()
        logger.info("Initialized repository at %s", self.db_path)

    def _ensure_schema(self) -> None:
        self.conn.execute(
            """
            CREATE TABLE IF NOT EXISTS recipes (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                title TEXT NOT NULL,
                ingredients TEXT NOT NULL,
                instructions TEXT,
                tags TEXT,
                servings INTEGER NOT NULL DEFAULT 1,
                created_at TEXT NOT NULL
            )
            """
        )
        self.conn.commit()

    def add(self, r: Recipe) -> int:
        cur = self.conn.execute(
            "INSERT INTO recipes (title, ingredients, instructions, tags, servings, created_at) VALUES (?, ?, ?, ?, ?, ?)",
            r.to_db_tuple(),
        )
        self.conn.commit()
        rid = cur.lastrowid
        logger.info("Added recipe %s (id=%s)", r.title, rid)
        return rid

    def update(self, r: Recipe) -> None:
        if r.id is None:
            raise ValueError("Recipe id required for update")
        self.conn.execute(
            "UPDATE recipes SET title=?, ingredients=?, instructions=?, tags=?, servings=? WHERE id=?",
            (r.title, json.dumps(r.ingredients, ensure_ascii=False), r.instructions, ",".join(r.tags), int(r.servings), r.id),
        )
        self.conn.commit()
        logger.info("Updated recipe id=%s title=%s", r.id, r.title)

    def delete(self, recipe_id: int) -> None:
        self.conn.execute("DELETE FROM recipes WHERE id=?", (recipe_id,))
        self.conn.commit()
        logger.info("Deleted recipe id=%s", recipe_id)

    def list_all(self) -> List[Recipe]:
        cur = self.conn.execute("SELECT * FROM recipes ORDER BY created_at DESC")
        return [self._row_to_recipe(r) for r in cur.fetchall()]

    def get(self, recipe_id: int) -> Optional[Recipe]:
        cur = self.conn.execute("SELECT * FROM recipes WHERE id=?", (recipe_id,))
        row = cur.fetchone()
        return self._row_to_recipe(row) if row else None

    def search(self, q: str = "") -> List[Recipe]:
        q_like = f"%{q}%"
        cur = self.conn.execute(
            "SELECT * FROM recipes WHERE title LIKE ? OR tags LIKE ? OR ingredients LIKE ? ORDER BY created_at DESC",
            (q_like, q_like, q_like),
        )
        return [self._row_to_recipe(r) for r in cur.fetchall()]

    def import_csv(self, path: Path) -> int:
        added = 0
        with open(path, newline="", encoding="utf-8") as fh:
            reader = csv.DictReader(fh)
            for row in reader:
                title = row.get("title", "").strip()
                if not title:
                    continue
                ingredients = [i.strip() for i in row.get("ingredients", "").split("|") if i.strip()]
                instructions = row.get("instructions", "").strip()
                tags = [t.strip() for t in row.get("tags", "").split(",") if t.strip()]
                servings = int(row.get("servings", "1") or 1)
                recipe = Recipe(None, title, ingredients, instructions, tags, servings, dt.datetime.utcnow().isoformat())
                self.add(recipe)
                added += 1
        logger.info("Imported %d recipes from %s", added, path)
        return added

    def export_csv(self, path: Path, recipes: Iterable[Recipe]) -> None:
        with open(path, "w", newline="", encoding="utf-8") as fh:
            writer = csv.DictWriter(fh, fieldnames=["id", "title", "ingredients", "instructions", "tags", "servings", "created_at"])
            writer.writeheader()
            for r in recipes:
                writer.writerow({
                    "id": r.id,
                    "title": r.title,
                    # ingredients stored separated by pipe to keep multiline-safe
                    "ingredients": "|".join(r.ingredients),
                    "instructions": r.instructions,
                    "tags": ",".join(r.tags),
                    "servings": r.servings,
                    "created_at": r.created_at,
                })
        logger.info("Exported recipes to %s", path)

    @staticmethod
    def _row_to_recipe(row: sqlite3.Row) -> Recipe:
        if row is None:
            raise ValueError("Row is None")
        ing_json = row["ingredients"] or "[]"
        try:
            ingredients = json.loads(ing_json)
        except Exception:
            # legacy: if not JSON, treat as newline separated string
            ingredients = [s.strip() for s in (ing_json or "").splitlines() if s.strip()]
        tags = [t.strip() for t in (row["tags"] or "").split(",") if t.strip()]
        return Recipe(
            id=int(row["id"]),
            title=row["title"],
            ingredients=ingredients,
            instructions=row["instructions"] or "",
            tags=tags,
            servings=int(row["servings"] or 1),
            created_at=row["created_at"] or dt.datetime.utcnow().isoformat(),
        )

    def close(self) -> None:
        self.conn.close()
        logger.info("Repository closed.")


# ---------------------------- GUI App ---------------------------- #
class RecipeBoxApp:
    def __init__(self, root: tk.Tk):
        self.root = root
        self.root.title(APP_NAME)
        self.repo = RecipeRepo()
        self._build_ui()
        self._load_list()

    def _build_ui(self):
        frame = ttk.Frame(self.root, padding=10)
        frame.pack(fill=tk.BOTH, expand=True)

        top = ttk.Frame(frame)
        top.pack(fill=tk.X)

        self.search_var = tk.StringVar()
        search_entry = ttk.Entry(top, textvariable=self.search_var, width=40)
        search_entry.pack(side=tk.LEFT, padx=(0, 6))
        search_entry.bind("<Return>", lambda e: self._on_search())

        ttk.Button(top, text="Search", command=self._on_search).pack(side=tk.LEFT)
        ttk.Button(top, text="Reset", command=self._on_reset).pack(side=tk.LEFT, padx=(6, 0))

        ttk.Button(top, text="New", command=self._open_new).pack(side=tk.RIGHT)
        ttk.Button(top, text="Import CSV", command=self._import_csv).pack(side=tk.RIGHT, padx=6)
        ttk.Button(top, text="Export CSV", command=self._export_csv).pack(side=tk.RIGHT)

        # Treeview
        cols = ("id", "title", "tags", "servings")
        self.tree = ttk.Treeview(frame, columns=cols, show="headings", height=14)
        self.tree.heading("id", text="ID")
        self.tree.heading("title", text="Title")
        self.tree.heading("tags", text="Tags")
        self.tree.heading("servings", text="Servings")
        self.tree.column("id", width=40, anchor="center")
        self.tree.column("title", width=300)
        self.tree.column("tags", width=150)
        self.tree.column("servings", width=80, anchor="center")
        self.tree.pack(fill=tk.BOTH, expand=True, pady=(8, 0))

        actions = ttk.Frame(frame)
        actions.pack(fill=tk.X, pady=(8, 0))
        ttk.Button(actions, text="View", command=self._on_view).pack(side=tk.LEFT)
        ttk.Button(actions, text="Edit", command=self._on_edit).pack(side=tk.LEFT, padx=6)
        ttk.Button(actions, text="Delete", command=self._on_delete).pack(side=tk.LEFT)
        ttk.Button(actions, text="Scale", command=self._on_scale).pack(side=tk.LEFT, padx=6)

        self.tree.bind("<Double-1>", lambda e: self._on_view())

    def _load_list(self, recipes: Optional[List[Recipe]] = None):
        if recipes is None:
            recipes = self.repo.list_all()
        self.tree.delete(*self.tree.get_children())
        for r in recipes:
            self.tree.insert("", tk.END, iid=str(r.id), values=(r.id, r.title, ", ".join(r.tags), r.servings))

    def _selected_id(self) -> Optional[int]:
        sel = self.tree.selection()
        if not sel:
            return None
        return int(sel[0])

    # ---------------- Actions ----------------
    def _on_search(self):
        q = self.search_var.get().strip()
        if not q:
            self._load_list()
            return
        results = self.repo.search(q)
        self._load_list(results)

    def _on_reset(self):
        self.search_var.set("")
        self._load_list()

    def _open_new(self):
        editor = RecipeEditor(self.root, self.repo)
        self.root.wait_window(editor.top)
        self._load_list()

    def _on_view(self):
        rid = self._selected_id()
        if not rid:
            messagebox.showinfo(APP_NAME, "Select a recipe to view.")
            return
        rec = self.repo.get(rid)
        if not rec:
            messagebox.showerror(APP_NAME, "Recipe not found.")
            return
        RecipeViewer(self.root, rec)

    def _on_edit(self):
        rid = self._selected_id()
        if not rid:
            messagebox.showinfo(APP_NAME, "Select a recipe to edit.")
            return
        rec = self.repo.get(rid)
        if not rec:
            messagebox.showerror(APP_NAME, "Recipe not found.")
            return
        editor = RecipeEditor(self.root, self.repo, rec)
        self.root.wait_window(editor.top)
        self._load_list()

    def _on_delete(self):
        rid = self._selected_id()
        if not rid:
            messagebox.showinfo(APP_NAME, "Select a recipe to delete.")
            return
        if messagebox.askyesno(APP_NAME, "Delete selected recipe?"):
            try:
                self.repo.delete(rid)
                self._load_list()
            except Exception as e:
                logger.exception("Delete failed: %s", e)
                messagebox.showerror(APP_NAME, f"Delete failed: {e}")

    def _on_scale(self):
        rid = self._selected_id()
        if not rid:
            messagebox.showinfo(APP_NAME, "Select a recipe to scale.")
            return
        rec = self.repo.get(rid)
        if not rec:
            messagebox.showerror(APP_NAME, "Recipe not found.")
            return
        ScaleDialog(self.root, rec)

    def _import_csv(self):
        path = filedialog.askopenfilename(filetypes=[("CSV files", "*.csv")], title="Import recipes CSV")
        if not path:
            return
        try:
            added = self.repo.import_csv(Path(path))
            messagebox.showinfo(APP_NAME, f"Imported {added} recipe(s).")
            self._load_list()
        except Exception as e:
            logger.exception("Import failed: %s", e)
            messagebox.showerror(APP_NAME, f"Import failed: {e}")

    def _export_csv(self):
        path = filedialog.asksaveasfilename(defaultextension=".csv", filetypes=[("CSV", "*.csv")], title="Export recipes to CSV")
        if not path:
            return
        try:
            recipes = self.repo.list_all()
            self.repo.export_csv(Path(path), recipes)
            messagebox.showinfo(APP_NAME, f"Exported {len(recipes)} recipe(s) to {path}")
        except Exception as e:
            logger.exception("Export failed: %s", e)
            messagebox.showerror(APP_NAME, f"Export failed: {e}")


# ------------------------- Recipe Editor ---------------------------- #
class RecipeEditor:
    def __init__(self, parent: tk.Tk, repo: RecipeRepo, recipe: Optional[Recipe] = None):
        self.repo = repo
        self.recipe = recipe
        self.top = tk.Toplevel(parent)
        self.top.title("Edit Recipe" if recipe else "New Recipe")
        self.top.transient(parent)
        self.top.grab_set()

        # Grid
        ttk.Label(self.top, text="Title:").grid(row=0, column=0, sticky="w", padx=6, pady=6)
        self.title_var = tk.StringVar(value=recipe.title if recipe else "")
        ttk.Entry(self.top, textvariable=self.title_var, width=60).grid(row=0, column=1, padx=6, pady=6, columnspan=3)

        ttk.Label(self.top, text="Servings:").grid(row=1, column=0, sticky="w", padx=6)
        self.servings_var = tk.StringVar(value=str(recipe.servings) if recipe else "1")
        ttk.Entry(self.top, textvariable=self.servings_var, width=6).grid(row=1, column=1, sticky="w", padx=6)

        ttk.Label(self.top, text="Tags (comma separated):").grid(row=1, column=2, sticky="w")
        self.tags_var = tk.StringVar(value=",".join(recipe.tags) if recipe else "")
        ttk.Entry(self.top, textvariable=self.tags_var, width=30).grid(row=1, column=3, sticky="w", padx=6)

        ttk.Label(self.top, text="Ingredients (one per line):").grid(row=2, column=0, sticky="nw", padx=6, pady=6)
        self.ing_text = tk.Text(self.top, width=60, height=8)
        self.ing_text.grid(row=2, column=1, columnspan=3, padx=6, pady=6)
        if recipe:
            self.ing_text.insert("1.0", "\n".join(recipe.ingredients))

        ttk.Label(self.top, text="Instructions:").grid(row=3, column=0, sticky="nw", padx=6, pady=6)
        self.inst_text = tk.Text(self.top, width=60, height=8)
        self.inst_text.grid(row=3, column=1, columnspan=3, padx=6, pady=6)
        if recipe:
            self.inst_text.insert("1.0", recipe.instructions)

        btn_frame = ttk.Frame(self.top)
        btn_frame.grid(row=4, column=0, columnspan=4, pady=(6, 12))
        ttk.Button(btn_frame, text="Save", command=self._on_save).pack(side=tk.LEFT, padx=6)
        ttk.Button(btn_frame, text="Cancel", command=self._on_cancel).pack(side=tk.LEFT)

        self.title_var.get()
        self.title_var  # silence linter

    def _validate(self) -> Tuple[bool, Optional[str]]:
        title = self.title_var.get().strip()
        if not title:
            return False, "Title cannot be empty."
        try:
            s = int(self.servings_var.get())
            if s < 1:
                return False, "Servings must be >= 1"
        except ValueError:
            return False, "Servings must be an integer."
        return True, None

    def _on_save(self):
        ok, err = self._validate()
        if not ok:
            messagebox.showerror("Validation error", err)
            return
        title = self.title_var.get().strip()
        servings = int(self.servings_var.get())
        tags = [t.strip() for t in self.tags_var.get().split(",") if t.strip()]
        ingredients = [line.strip() for line in self.ing_text.get("1.0", tk.END).splitlines() if line.strip()]
        instructions = self.inst_text.get("1.0", tk.END).strip()
        now = dt.datetime.utcnow().isoformat()
        if self.recipe:
            updated = Recipe(self.recipe.id, title, ingredients, instructions, tags, servings, self.recipe.created_at)
            try:
                self.repo.update(updated)
                messagebox.showinfo("Saved", "Recipe updated.")
            except Exception as e:
                logger.exception("Update failed: %s", e)
                messagebox.showerror("Error", str(e))
        else:
            new = Recipe(None, title, ingredients, instructions, tags, servings, now)
            try:
                self.repo.add(new)
                messagebox.showinfo("Saved", "Recipe added.")
            except Exception as e:
                logger.exception("Add failed: %s", e)
                messagebox.showerror("Error", str(e))
        self.top.destroy()

    def _on_cancel(self):
        self.top.destroy()


# ------------------------- Recipe Viewer ---------------------------- #
class RecipeViewer:
    def __init__(self, parent: tk.Tk, recipe: Recipe):
        self.top = tk.Toplevel(parent)
        self.top.title(recipe.title)
        self.top.transient(parent)
        self.top.grab_set()

        frm = ttk.Frame(self.top, padding=10)
        frm.pack(fill=tk.BOTH, expand=True)

        ttk.Label(frm, text=recipe.title, font=("TkDefaultFont", 14, "bold")).pack(anchor="w")
        meta = f"Servings: {recipe.servings}    Tags: {', '.join(recipe.tags)}    Created: {recipe.created_at.split('T')[0]}"
        ttk.Label(frm, text=meta, foreground="gray").pack(anchor="w", pady=(0, 8))

        ttk.Label(frm, text="Ingredients:", font=("TkDefaultFont", 10, "bold")).pack(anchor="w")
        ing_text = tk.Text(frm, width=60, height=8)
        ing_text.pack(pady=(0, 8))
        ing_text.insert("1.0", "\n".join(recipe.ingredients))
        ing_text.configure(state="disabled")

        ttk.Label(frm, text="Instructions:", font=("TkDefaultFont", 10, "bold")).pack(anchor="w")
        inst_text = tk.Text(frm, width=60, height=10)
        inst_text.pack(pady=(0, 8))
        inst_text.insert("1.0", recipe.instructions)
        inst_text.configure(state="disabled")

        ttk.Button(frm, text="Close", command=self.top.destroy).pack(anchor="e", pady=(6, 0))


# ------------------------- Scale Dialog ---------------------------- #
class ScaleDialog:
    def __init__(self, parent: tk.Tk, recipe: Recipe):
        self.recipe = recipe
        self.top = tk.Toplevel(parent)
        self.top.title(f"Scale: {recipe.title}")
        self.top.transient(parent)
        self.top.grab_set()

        frm = ttk.Frame(self.top, padding=10)
        frm.pack(fill=tk.BOTH, expand=True)

        ttk.Label(frm, text=f"Original servings: {recipe.servings}").pack(anchor="w", pady=(0, 6))
        ttk.Label(frm, text="New servings:").pack(anchor="w")
        self.new_serv_var = tk.StringVar(value=str(recipe.servings))
        ttk.Entry(frm, textvariable=self.new_serv_var, width=8).pack(anchor="w", pady=(0, 6))

        ttk.Label(frm, text="Scaled ingredients (best-effort numeric scaling):").pack(anchor="w", pady=(6, 0))
        self.result_text = tk.Text(frm, width=60, height=12)
        self.result_text.pack(pady=(4, 6))

        btns = ttk.Frame(frm)
        btns.pack(fill=tk.X)
        ttk.Button(btns, text="Scale", command=self._on_scale).pack(side=tk.LEFT, padx=6)
        ttk.Button(btns, text="Close", command=self.top.destroy).pack(side=tk.RIGHT)

    def _on_scale(self):
        try:
            new_serv = int(self.new_serv_var.get())
            if new_serv < 1:
                raise ValueError("Servings must be >= 1")
        except ValueError as e:
            messagebox.showerror("Error", str(e))
            return

        ratio = new_serv / self.recipe.servings if self.recipe.servings else 1
        scaled = []
        for line in self.recipe.ingredients:
            # try to scale a leading number (e.g., "1 1/2 cup sugar" or "2 cups")
            scaled_line = _scale_ingredient_line(line, ratio)
            scaled.append(scaled_line)
        self.result_text.delete("1.0", tk.END)
        self.result_text.insert("1.0", "\n".join(scaled))


# -------------------- Scaling Helper (best effort) ---------------------- #
def _scale_ingredient_line(line: str, ratio: float) -> str:
    """
    Attempt to scale leading numeric quantities. This is a heuristic:
    - Handles integers and simple fractions like '1/2' and mixed '1 1/2'.
    - If no leading number found, returns the original line.
    """
    import re
    s = line.strip()
    # match mixed number (1 1/2), fraction (1/2), or decimal (1.5) or integer (2)
    m = re.match(r"^(\d+\s+\d+/\d+|\d+/\d+|\d+(?:\.\d+)?)\b(.*)$", s)
    if not m:
        return s  # nothing numeric to scale
    qty_str, rest = m.group(1).strip(), m.group(2).strip()
    # convert qty_str to float
    def frac_to_float(q):
        if " " in q:
            a, b = q.split()
            return float(a) + _frac_str_to_float(b)
        if "/" in q:
            return _frac_str_to_float(q)
        return float(q)
    def _frac_str_to_float(fr):
        num, den = fr.split("/")
        return float(num) / float(den)
    try:
        qty = frac_to_float(qty_str)
        new_qty = qty * ratio
        # Format: if close to integer show int, else 2 decimals
        if abs(new_qty - round(new_qty)) < 1e-6:
            qty_fmt = str(int(round(new_qty)))
        else:
            qty_fmt = f"{new_qty:.2f}".rstrip("0").rstrip(".")
        return f"{qty_fmt} {rest}"
    except Exception:
        return s


# ------------------------- Application Entry --------------------------- #
def main():
    logger.info("Starting %s", APP_NAME)
    root = tk.Tk()
    try:
        ttk.Style().theme_use("clam")
    except Exception:
        pass
    app = RecipeBoxApp(root)

    # seed sample data if empty
    if not app.repo.list_all():
        try:
            sample = Recipe(
                None,
                "Pancakes",
                ["1 1/2 cups all-purpose flour", "3 1/2 tsp baking powder", "1 tsp salt", "1 tbsp sugar", "1 1/4 cups milk", "1 egg", "3 tbsp butter, melted"],
                "Mix dry ingredients. Add milk and egg. Cook on hot griddle.",
                ["breakfast", "easy"],
                4,
                dt.datetime.utcnow().isoformat(),
            )
            app.repo.add(sample)
            app._load_list()
        except Exception:
            logger.exception("Failed to seed sample")

    try:
        root.mainloop()
    finally:
        app.repo.close()
        logger.info("Exiting %s", APP_NAME)


if __name__ == "__main__":
    main()
