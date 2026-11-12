# Shot 5a: the agent key list, read keylessly from NEAR testnet RPC (the README curl, pretty-printed).
Output submission/media/raw/05a_keys.mp4
Output submission/media/raw/05a_keys.txt
Source submission/media/tapes/settings.tape
Type `curl -s https://rpc.testnet.fastnear.com -H 'Content-Type: application/json' -d '{"jsonrpc":"2.0","id":1,"method":"query","params":{"request_type":"view_access_key_list","finality":"final","account_id":"agent.anchorium-team.testnet"}}' | python3 -m json.tool`
Sleep 400ms
Enter
Sleep 6s
Screenshot submission/media/raw/05a_keys_end.png
Sleep 1s
