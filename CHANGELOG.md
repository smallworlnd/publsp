# Changelog

## [v0.4.10] - 2025-06-18

- added opt-in feature to include a node signature generated from the nostr pubkey in ads in order to help clients distinguish authentic ads from spam/fraud (which is not yet a problem as of this commit)

## [v0.4.9] - 2025-06-17

- added lease record keeping in a json file; modified customer side filtering logic

## [v0.4.8] - 2025-06-15

- refactored for graceful error handling in response objects and shutdown scripts (cf78e4c)

## [v0.4.7] - 2025-06-15

- added hot loading for value prop and nostr relays; modified hot loading order of operations and added safety checks (d8bf56c)

## [v0.4.6] - 2025-06-14

- added additional error handling on channel open for debugging edge cases (4713cab)

## [v0.4.5] - 2025-06-11

- added LSP utxo set error handling and client notification, and added respective cli logging output (fd2a3c7)

## [v0.4.4] - 2025-06-10

- fixed cost calculation regression bug; made logging output format consistent with rust-nostr (c0241d0)
- included relay logs in output for improved debugging; now use defaults for channel conf times for improved UX until better controls built (1efccca)

## [v0.4.2] - 2025-06-07

- modified API to accept target confs (9c1cfd3)
- updated README with description of hot-reloading using the .env file (b8445cd)
- new feature: hot-reloading of .env while running daemon mode for dynamically updating ads (625c035)

## [v0.3.3] - 2025-06-06

- fixed lease cost calculation bug (de7d8be)

## [v0.3.2] - 2025-06-06

- refactored cost calculations, added lease duration component; updated README with macaroon permissions instructions (6d0edb9)
- fixed node property median fee value typing bug (763d6c0)

## [v0.3.0] - 2025-06-04

- publsp can now be run in daemon mode for easier automating/scripting (8dec5ba)
- fixed invoice amount verification bug from incorrect Decimal handling on customer side (e8210fa)
- fixed formatting bug to show correct output (7607b55)

## [v0.2.2] - 2025-05-30

- bump nostr-sdk v0.42.1 (648ac1b)

## [v0.2.1] - 2025-05-30

- removed redundant relay requests; improved nostr keys path handling (8dc17ba)

## [v0.2.0] - 2025-05-30

- new feature: built fastapi for customer-side workflow (4e2d3d1)

## [v0.1.8] - 2025-05-26

- modified OrderResponseHandler to run with either the CLI or the (new) API-friendly workflow (1d5ad5d)

## [v0.1.7] - 2025-05-18

- exposing KeyHandler options to NostrClient for finer grained API (03d279e)

## [v0.1.6] - 2025-05-18

- added external setting to choose key encryption (b8406c8)

## [v0.1.5] - 2025-05-17

- fixed KeyHandler init bug for ephemeral key generation (4c5f9a8)

## [v0.1.4] - 2025-05-16

- modified KeyHandler for easier ephemeral key generation (5e787c6)

## [v0.1.3] - 2025-05-16

- added cli option to reuse nostr keys, default is auto key rotation (21cdce8)

## [v0.1.2] - 2025-05-12

- fix: added pay invoice helper, fixed build (081e26d)

## [v0.1.1] - 2025-05-12

- fix(pyproject.toml): fixed file to build script (bdfcb40)

## [v0.1.0] - 2025-05-11

- first commit (5aec57a)
