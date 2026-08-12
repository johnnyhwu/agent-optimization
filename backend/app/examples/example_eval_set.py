"""Example eval-set script.

Upload a single .py file. The system calls `main(database_handler)` and uses what
it returns; everything else in the file is yours to organise however you like.

The one argument you get is `database_handler`. It has one method:

    database_handler.run_sql(sql, params=None) -> list[dict]

`params` is optional and uses the same placeholders psycopg does — `%s` with a
tuple, or `%(name)s` with a dict. Use it instead of building SQL with f-strings:
it gets the quoting right for dates, NULLs and any value containing an
apostrophe, which is where these scripts usually go wrong.

The connection is READ-ONLY. Writes are refused by the database itself, so an
accidental UPDATE cannot damage anything.

Limits, so a runaway query cannot take the system down with it:

  * 50,000 rows per query        -> raises, so you never build an eval set from
                                    half the data without noticing
  * 30 seconds per statement     -> raises
  * 60 seconds for the whole run -> the script is stopped
  * 3,000 rows returned          -> the rest are dropped, and you are told

Besides the standard library, two third-party packages are installed and can be
imported: **pandas** and **tabulate** (numpy comes with pandas). Anything else
will not be found — the list is deliberately short, and lives in
`backend/requirements-scripts.txt`.

`print()` anything you like; the output comes back with the results.

`main()` must return a list of dicts with these keys:

    question                                     (required)
    ground_truth_response                        (required)
    ground_truth_reasoning_process_description   (required)
    skill                                        (required, a list of strings)
    question_id                                  (optional, generated if absent)
"""


def fetch_billing_questions(database_handler, quarter):
    """Ordinary helper — split the script up however suits you."""
    return database_handler.run_sql(
        """
        SELECT customer_name,
               closing_balance,
               quarter
          FROM billing_summary
         WHERE quarter = %(quarter)s
           AND closing_balance > 0
         ORDER BY closing_balance DESC
         LIMIT 100
        """,
        {"quarter": quarter},
    )


def main(database_handler) -> list[dict]:
    rows = fetch_billing_questions(database_handler, "2026Q2")
    print(f"fetched {len(rows)} billing rows")

    questions = []
    for row in rows:
        questions.append(
            {
                "question": (
                    f"How much did {row['customer_name']} owe at the end of "
                    f"{row['quarter']}?"
                ),
                "ground_truth_response": f"${row['closing_balance']:,.2f}",
                "ground_truth_reasoning_process_description": (
                    "Read the billing skill, query billing_summary for that "
                    "customer and quarter, and report the closing balance."
                ),
                "skill": ["billing"],
            }
        )

    print(f"returning {len(questions)} questions")
    return questions
