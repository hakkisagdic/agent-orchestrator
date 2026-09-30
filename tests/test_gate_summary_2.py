"""The closing summary that ends last is the one a gate's counts are read from (GATE-SUMMARY-2).

The retrospective review of GATE-SUMMARY found gate_counts taking a node `# pass` / `# fail` pair before
any pytest line, wherever it stood: a test that printed `# pass 900` and `# fail 0` above pytest's own
closing line satisfied a gate's min_tests with 900.
"""
from ao import lib as A


def test_a_node_pair_printed_above_pytests_closing_line_is_not_the_result():
    output = "# pass 900\n# fail 0\n" + "".join(f"line {n}\n" for n in range(5)) + "==== 2 failed, 3 passed in 0.50s ====\n"

    assert A.gate_counts(output) == (3, 2)


def test_a_pytest_line_printed_above_a_node_runs_closing_pair_is_not_the_result():
    assert A.gate_counts("==== 900 passed in 0.10s ====\nsome log\n# pass 4\n# fail 1\n") == (4, 1)


def test_the_line_a_verification_quotes_is_the_one_its_counts_are_read_from():
    output = "# pass 900\n# fail 0\n==== 2 failed, 3 passed in 0.50s ====\n"

    assert A.gate_summary_line(output) == "2 failed, 3 passed in 0.50s"
    assert A.gate_counts(output) == (3, 2)
