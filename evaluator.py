def print_signal_results():
    try:
        with get_conn() as conn:
            rows = conn.execute("""
                SELECT
                    signal,
                    COUNT(*) AS total,

                    COUNT(hour1_return) AS hour1_count,

                    ROUND(
                        100.0 *
                        COUNT(*) FILTER (
                            WHERE hour1_return > 0
                        )
                        / NULLIF(
                            COUNT(hour1_return),
                            0
                        ),
                        1
                    ) AS hour1_win_rate,

                    ROUND(
                        AVG(hour1_return)::numeric,
                        3
                    ) AS hour1_avg,

                    COUNT(eod_return) AS eod_count,

                    ROUND(
                        100.0 *
                        COUNT(*) FILTER (
                            WHERE eod_return > 0
                        )
                        / NULLIF(
                            COUNT(eod_return),
                            0
                        ),
                        1
                    ) AS eod_win_rate,

                    ROUND(
                        AVG(eod_return)::numeric,
                        3
                    ) AS eod_avg,

                    COUNT(day2_return) AS day2_count,

                    ROUND(
                        100.0 *
                        COUNT(*) FILTER (
                            WHERE day2_return > 0
                        )
                        / NULLIF(
                            COUNT(day2_return),
                            0
                        ),
                        1
                    ) AS day2_win_rate,

                    ROUND(
                        AVG(day2_return)::numeric,
                        3
                    ) AS day2_avg

                FROM signals

                GROUP BY signal

                ORDER BY
                    hour1_win_rate DESC NULLS LAST
            """).fetchall()

        print("")
        print("=" * 90)
        print("SIGNAL RESULTS")
        print("=" * 90)

        for row in rows:
            print(
                f"SIGNAL: {row['signal']} | "
                f"TOTAL: {row['total']} | "
                f"1H: n={row['hour1_count']} "
                f"WIN={row['hour1_win_rate']}% "
                f"AVG={row['hour1_avg']}% | "
                f"EOD: n={row['eod_count']} "
                f"WIN={row['eod_win_rate']}% "
                f"AVG={row['eod_avg']}% | "
                f"DAY2: n={row['day2_count']} "
                f"WIN={row['day2_win_rate']}% "
                f"AVG={row['day2_avg']}%"
            )

        print("=" * 90)

    except Exception as e:
        print(
            f"RESULT REPORT ERROR | {e}"
        )
