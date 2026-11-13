# Shot 9 (NEW): optional fiat on-ramp, keyless (no PayPal keys -> offline simulation; NEAR AI key blanked so no
# billable call). The real sandbox capture of 12 Nov is documented in docs/FIAT_ONRAMP.md.
Output submission/media/raw/09_fiat.mp4
Output submission/media/raw/09_fiat.txt
Source submission/media/tapes/settings.tape
Hide
Type "export NEAR_AI_API_KEY= && clear"
Enter
Sleep 300ms
Show
Type "make -s paypal-demo 2>&1 | sed -n 8,15p"
Sleep 400ms
Enter
Sleep 28s
Screenshot submission/media/raw/09_fiat_end.png
Sleep 1s
