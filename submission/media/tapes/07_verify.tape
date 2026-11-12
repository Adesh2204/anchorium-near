# Shot 7 / gallery 11 timing: a fresh clone from GitHub in an empty temp dir, timed setup + demo, then both test suites.
# Hidden setup: an empty temp dir, cargo on PATH, and CARGO_TARGET_DIR pointing at the existing build cache so the
# contract tests do not recompile the same sources for minutes (results are identical; the trees are the same).
Output submission/media/raw/07_verify.mp4
Output submission/media/raw/07_verify.txt
Source submission/media/tapes/settings.tape
Hide
Type "export PATH=$HOME/.cargo/bin:$PATH CARGO_TARGET_DIR=$PWD/contracts/anchorium-guard/target && cd $(mktemp -d) && clear"
Enter
Sleep 500ms
Show
Type "git clone -q https://github.com/Adesh2204/anchorium-near && cd anchorium-near"
Sleep 300ms
Enter
Sleep 12s
Type "time (make setup && make demo)"
Sleep 300ms
Enter
Sleep 150s
Screenshot submission/media/raw/07_verify_demo.png
Type "clear && make test-py 2>&1 | tail -2 && make contract-test 2>&1 | grep 'test result'"
Sleep 300ms
Enter
Sleep 120s
Screenshot submission/media/raw/07_verify_end.png
Sleep 1s
