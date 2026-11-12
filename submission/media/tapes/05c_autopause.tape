# Shot 5c: auto-pause at the daily loss cap, proven by the real sandbox test on a local NEAR node (label: local sandbox test).
Output submission/media/raw/05c_autopause.mp4
Output submission/media/raw/05c_autopause.txt
Source submission/media/tapes/settings.tape
Hide
Type "export PATH=$HOME/.cargo/bin:$PATH && cd contracts/anchorium-guard && clear"
Enter
Sleep 300ms
Show
Type "sed -n 603,612p src/lib.rs"
Sleep 300ms
Enter
Sleep 2s
Type "cargo test -q --test sandbox guard_rules_kill_switch_and_auto_pause_on_chain"
Sleep 300ms
Enter
Sleep 50s
Screenshot submission/media/raw/05c_autopause_end.png
Sleep 1s
