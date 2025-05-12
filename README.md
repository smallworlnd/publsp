# publsp

A FOSS CLI tool for any Lightning Network node or Lightning Service Provider (LSP) to advertise liquidity offers over Nostr. This is a fairly rough adaptation of [bLIP51](https://github.com/lightning/blips/blob/master/blip-0051.md) that also takes inspiration from [NIP-15](https://github.com/nostr-protocol/nips/blob/master/15.md), [NIP-69](https://github.com/nostr-protocol/nips/blob/master/69.md) and [NIP-99](https://github.com/nostr-protocol/nips/blob/master/69.md). 

The tl;dr:
LSPs advertise liquidity as addressable Kind 39735 events. Customers just pull and evaluate all those structured events, then NIP-17 DM an LSP of their choice to coordinate a liquidity purchase.

As a customer, use `publsp` to find an LSP that suits your needs and then request a quote on the spot. Use whatever wallet of your choice to pay the invoice, and you get liquidity. You're paying a [hodl invoice](https://lightningwiki.net/index.php/HODL_Invoice), so the payment preimage is held by the LSP until the channel opens.

As an LSP, use `publsp` to post a liquidity offer then let the daemon run the rest. It will 1) listen for liquidity requests, 2) send invoices according the customer liquidity needs, 3) send funding transactions to the mempool upon payment, 4) update the customer on channel funding details, and 5) release the payment preimage upon channel open.

Currently only supports `lnd`, `cln` to come in the future. `publsp` also only allows LSPs to post one liquidity offer (the same as every other existing service), but can easily be extended in the future to many different advertisements for a variety of use cases.

## Why Nostr as a LN liquidity marketplace?

- Infinitely extensible in the future in ways unimaginable right now, especially with the advent of AI agents participating in the open market
- Existing LSPs on other platforms/protocols/APIs can easily plug into Nostr, and it may be to their detriment not to in the future!
- Free to use (free, as in beer, **and** free as in freedom) for both customer and LSP, you're not locked in
- Open to any customer and LSP
- Transparent advertising: what you see is what you get, and is enforced on client-side (don't trust, verify)
- uncensorable, no one can stop you from advertising or purchasing
- Decentralized, no single API point of failure, and liquidity offers persist
- Orders can be privately made through NIP-17 DMs, so order flows are not broadcast and not centralized

## Components under the hood

### LSP/server side

- Generate and use Nostr keys
- Generate liquidity orders roughly following the [bLIP51 spec](https://github.com/lightning/blips/blob/master/blip-0051.md)
- Push/update offers to relays using Nostr keys
- Listen for, and handle, purchase request events from clients

### Client/customer side

- Generate and use Nostr keys
- Pull all Kind 39735 events and filter for the structured liquidity offer tags
- Generate cost summaries for different capacities (if desired)
- Request liquidity from an LSP discovered through its Kind 39735 event(s)

* Paying the LSP's invoice needs to be with the customer's wallet of choice since `publsp` currently does not support any wallets yet

## Order of operations

- LSPs post their liquidity offers
- Client requests all available offers
- Client initiates an order with the desired LSP over Nostr
  * includes the target pubkey URI that will receive the channel open
- LSP attempts a connection to the client's desired pubkey URI (otherwise transaction halts)
- Client verifies the BOLT11 destination pubkey and amount matches the those listed in the Nostr advertisement (otherwise transaction halts)
- Client pays invoice using the wallet of their choice
- LSP opens channel, sends private message to customer with funding details
- LSP releases preimage of the hodl invoice after channel open confirmation

## Trust model

Following the bLIP51 spec implies we inherit the same trust model; client pays the LSP first (hodl invoice) then receives, so the risk is that the client pays and does not receive. LSP reputation is on the line so bad behavior could generally be disincentivized.

## Installation

### Via PyPI

```bash
pip install publsp
```

### Via Docker

```bash
git clone https://github.com/smallworlnd/publsp.git
cd publsp
docker build -t publsp .
```

## Quickstart

```bash
# See top-level help
publsp --help

# As a customer, show all the available options & defaults to search and request liquidity
publsp customer --help

# As an LSP, show all available options & defaults for defining your offer
publsp lsp --help
```

## Usage

### LSP Mode

```bash
# minimally
publsp lsp \
  --node lnd \
  --rest-host http://127.0.0.1:8081 \
  --permissions-file-path /path/to/admin.macaroon \
  --cert-file-path /path/to/tls.cert
```
This will drop you into an interactive REPL to:
1. Publish or update your ad
2. View your current active ad
3. Inactivate your ad
4. Exit

Leave this running after you've published an ad in order to listen for, and automatically process, order requests.

### Customer Mode

```bash
publsp customer --target-pubkey-uri pubkey@host:port
```
You’ll see a prompt to:
1. Show discovered ads
2. Get liquidity cost breakdown
3. Request a channel
4. Exit

### Advanced usage

You can make use of a `.env` file to specify your parameters to avoid having to write out long command lines. Have a look at the `.env.example` file.
