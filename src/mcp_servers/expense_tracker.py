import os
import sqlite3
import warnings
import logging
from typing import Optional
from datetime import datetime, date, timedelta
from dotenv import load_dotenv
from fastmcp import FastMCP
load_dotenv()

warnings.filterwarnings("ignore")
logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

BASE_DIR = os.path.dirname(os.path.abspath(__file__))
DB_PATH = os.path.join(BASE_DIR, "expenses.db")

mcp = FastMCP("Expense Tracker Server")

def parse_expense_date(date_string: str) -> date:
    """Parses an expense date from an ISO datetime or date string."""
    try:
        return datetime.fromisoformat(date_string).date()
    except ValueError as e:
        raise ValueError(f"Invalid expense date '{date_string}'. Use YYYY-MM-DD, YYYY-MM-DD HH:MM:SS, or YYYY-MM-DDTHH:MM:SS format.") from e

def get_connection():
    """Creates and returns a connection to the SQLite expense database."""
    return sqlite3.connect(DB_PATH)

def init_db():
    """Initializes the expense database by creating the required tables if they do not already exist."""
    with get_connection() as conn:
        conn.execute("""
            CREATE TABLE IF NOT EXISTS expenses(
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                start_date TEXT NOT NULL,
                end_date TEXT,
                amount REAL NOT NULL,
                category TEXT NOT NULL,
                note TEXT DEFAULT '')
        """)

init_db()

@mcp.tool(name="Add-Expense")
def add_expense( start_date: Optional[str] = None, amount: float = 0.0, category: str = "Miscellaneous", note: str = "", end_date: Optional[str] = None ):
    """
    Records a new expense in the database.
    Use this tool to log, save, or track a new financial transaction.

    Args:
        amount: The monetary value of the expense. (Default: 0)
        category: The classification of the expense. (Default: "Miscellaneous")
        start_date: The date when the expense occurred or started, in ISO 8601 format. (Default: current date)
        note: Optional context or description of the expense. (Default: "")
        end_date: Optional ending date for a recurring or multi-day expense, in ISO 8601 format. (Default: None)

    Returns:
        The recorded expense data, including its database ID.
    """

    if start_date is None:
        start_date = date.today().strftime("%Y-%m-%d")
    else:
        try:
            parse_expense_date(start_date)
        except ValueError as e:
            logger.warning(str(e))
            raise

    if end_date is not None:
        try:
            parse_expense_date(end_date)
        except ValueError as e:
            logger.warning(str(e))
            raise

    if amount < 0:
        raise ValueError("'amount' must be greater than or equal to zero.")

    try:
        with get_connection() as conn:
            cursor = conn.execute(
                """
                INSERT INTO expenses(start_date, end_date, amount, category, note)
                VALUES (?, ?, ?, ?, ?)
                """,
                (start_date, end_date, amount, category, note),
            )

        expense = {"id": cursor.lastrowid, "start_date": start_date, "end_date": end_date, "amount": amount, "category": category, "note": note}

        logger.info("Expense recorded successfully.")

        return {
            "expense": expense
        }

    except sqlite3.Error as e:
        logger.exception("Expense database operation failed.")
        raise RuntimeError(f"Expense database operation failed: {str(e)}") from e

@mcp.tool(name="List-Expenses")
def list_expenses( start_date: Optional[str] = None, end_date: Optional[str] = None ):
    """
    Retrieves recorded expenses from the database.

    Args:
        start_date: Optional starting date for the expense search, in ISO 8601 format. (Default: None, all expenses)
        end_date: Optional ending date for the expense search, in ISO 8601 format. (Default: None, all expenses)

    Returns:
        A list of expense records matching the specified date filters.
    """

    try:
        if start_date:
            parse_expense_date(start_date)

        if end_date:
            parse_expense_date(end_date)

    except ValueError as e:
        logger.warning(str(e))
        raise

    query = """
        SELECT id, start_date, end_date, amount, category, note
        FROM expenses
    """

    conditions = []
    params = []

    if start_date:
        conditions.append("start_date >= ?")
        params.append(start_date)

    if end_date:
        conditions.append("start_date <= ?")
        params.append(end_date)

    if conditions:
        query += " WHERE " + " AND ".join(conditions)

    query += """
        ORDER BY start_date ASC, id ASC
    """

    try:
        with get_connection() as conn:
            cursor = conn.execute(query, params)
            rows = cursor.fetchall()

        expenses = [{"id": row[0], "start_date": row[1], "end_date": row[2], "amount": row[3], "category": row[4], "note": row[5] } for row in rows]

        logger.info("Expenses retrieved successfully.")

        return {
            "expenses": expenses
        }

    except sqlite3.Error as e:
        logger.exception("Expense database operation failed.")
        raise RuntimeError(f"Expense database operation failed: {str(e)}") from e

@mcp.tool(name="Summarize-Expenses")
def summarize_expenses( start_date: Optional[str] = None, end_date: Optional[str] = None, category: Optional[str] = None ):
    """
    Generates a spending summary grouped by category for a specified period.
    Use this tool to calculate total spending, category breakdowns, or aggregated expense reports.

    Args:
        start_date: Optional starting date for the expense summary, in ISO 8601 format. (Default: None, all expenses)
        end_date: Optional ending date for the expense summary, in ISO 8601 format. (Default: None, all expenses)
        category: Optional expense category filter. (Default: None, all categories)

    Returns:
        A dictionary containing the total amount spent per category.
    """

    try:
        if start_date:
            parse_expense_date(start_date)

        if end_date:
            parse_expense_date(end_date)

    except ValueError as e:
        logger.warning(str(e))
        raise

    query = """
        SELECT category,
        SUM(amount) AS total_amount
        FROM expenses
    """

    conditions = []
    params = []

    if start_date:
        conditions.append("start_date >= ?")
        params.append(start_date)

    if end_date:
        conditions.append("start_date <= ?")
        params.append(end_date)

    if category:
        conditions.append("category = ?")
        params.append(category)

    if conditions:
        query += " WHERE " + " AND ".join(conditions)

    query += """
        GROUP BY category
        ORDER BY category ASC
    """

    try:
        with get_connection() as conn:
            cursor = conn.execute(query, params)
            rows = cursor.fetchall()

        summary = [{"category": row[0], "total_amount": row[1]} for row in rows]

        logger.info("Expense summary retrieved successfully.")

        return {
            "summary": summary
        }

    except sqlite3.Error as e:
        logger.exception("Expense database operation failed.")
        raise RuntimeError(f"Expense database operation failed: {str(e)}") from e

if __name__ == "__main__":
    mcp.run(transport="stdio")