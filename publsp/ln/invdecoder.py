# ripped from
# https://github.com/rustyrussell/lightning-payencode/blob/master/lnaddr.py and
# adapted for the purpose of decoding BOLT11 invoices on the
# customer/client-side so they don't need a LN node just to browse and purchase
# channels

from publsp.ln.bech32 import bech32_decode, CHARSET
from binascii import hexlify
from decimal import Decimal

import bitstring
import re
import secp256k1
import time


def unshorten_amount(amount):
    """ Given a shortened amount, convert it into a decimal
    # BOLT #11:
    # The following `multiplier` letters are defined:
    #
    #* `m` (milli): multiply by 0.001
    #* `u` (micro): multiply by 0.000001
    #* `n` (nano): multiply by 0.000000001
    #* `p` (pico): multiply by 0.000000000001
    """
    units = {
        'p': 10**12,
        'n': 10**9,
        'u': 10**6,
        'm': 10**3,
    }
    unit = str(amount)[-1]
    # BOLT #11:
    # A reader SHOULD fail if `amount` contains a non-digit, or is followed by
    # anything except a `multiplier` in the table above.
    if not re.fullmatch(r"\d+[pnum]?", str(amount)):
        raise ValueError("Invalid amount '{}'".format(amount))

    if unit in units.keys():
        return Decimal(amount[:-1]) / units[unit]
    else:
        return Decimal(amount)


# Bech32 spits out array of 5-bit values.  Shim here.
def u5_to_bitarray(arr):
    ret = bitstring.BitArray()
    for a in arr:
        ret += bitstring.pack("uint:5", a)
    return ret


def bitarray_to_u5(barr):
    assert barr.len % 5 == 0
    ret = []
    s = bitstring.ConstBitStream(barr)
    while s.pos != s.len:
        ret.append(s.read(5).uint)
    return ret


# Tagged field containing BitArray
def tagged(char, l):
    # Tagged fields need to be zero-padded to 5 bits.
    while l.len % 5 != 0:
        l.append('0b0')
    return bitstring.pack("uint:5, uint:5, uint:5",
                          CHARSET.find(char),
                          (l.len / 5) / 32, (l.len / 5) % 32) + l


# Discard trailing bits, convert to bytes.
def trim_to_bytes(barr):
    # Adds a byte if necessary.
    b = barr.tobytes()
    if barr.len % 8 != 0:
        return b[:-1]
    return b


# Try to pull out tagged data: returns tag, tagged data and remainder.
def pull_tagged(stream):
    tag = stream.read(5).uint
    length = stream.read(5).uint * 32 + stream.read(5).uint
    return (CHARSET[tag], stream.read(length * 5), stream)


def lndecode(a):
    hrp, data = bech32_decode(a)
    if not hrp:
        raise ValueError("Bad bech32 checksum")

    # BOLT #11:
    #
    # A reader MUST fail if it does not understand the `prefix`.
    if not hrp.startswith('ln'):
        raise ValueError("Does not start with ln")

    data = u5_to_bitarray(data)

    # Final signature 65 bytes, split it off.
    if len(data) < 65*8:
        raise ValueError("Too short to contain signature")
    sigdecoded = data[-65*8:].tobytes()
    data = bitstring.ConstBitStream(data[:-65*8])

    addr = LnAddr()
    addr.pubkey = None

    m = re.search(r"[^\d]+", hrp[2:])
    if m:
        addr.currency = m.group(0)
        amountstr = hrp[2+m.end():]
        # BOLT #11:
        #
        # A reader SHOULD indicate if amount is unspecified, otherwise it MUST
        # multiply `amount` by the `multiplier` value (if any) to derive the
        # amount required for payment.
        if amountstr != '':
            addr.amount = unshorten_amount(amountstr)

    addr.date = data.read(35).uint

    while data.pos != data.len:
        tag, tagdata, data = pull_tagged(data)
        # BOLT #11:
        #
        # * `r` (3): `data_length` variable.  One or more entries
        # containing extra routing information for a private route;
        # there may be more than one `r` field, too.
        #    * `pubkey` (264 bits)
        #    * `short_channel_id` (64 bits)
        #    * `feebase` (32 bits, big-endian)
        #    * `feerate` (32 bits, big-endian)
        #    * `cltv_expiry_delta` (16 bits, big-endian)
        route = []
        s = bitstring.ConstBitStream(tagdata)
        while s.pos + 264 + 64 + 32 + 32 + 16 < s.len:
            route.append((s.read(264).tobytes(),
                          s.read(64).tobytes(),
                          s.read(32).intbe,
                          s.read(32).intbe,
                          s.read(16).intbe))
        addr.tags.append(('r', route))

    # BOLT #11:
    #
    # A reader MUST check that the `signature` is valid (see the `n` tagged
    # field specified below).
    if addr.pubkey:  # Specified by `n`
        # BOLT #11:
        #
        # A reader MUST use the `n` field to validate the signature instead of
        # performing signature recovery if a valid `n` field is provided.
        addr.signature = addr.pubkey.ecdsa_deserialize_compact(sigdecoded[0:64])
        if not addr.pubkey.ecdsa_verify(bytearray([ord(c) for c in hrp]) + data.tobytes(), addr.signature):
            raise ValueError('Invalid signature')
    else:  # Recover pubkey from signature.
        addr.pubkey = secp256k1.PublicKey()
        addr.signature = addr.pubkey.ecdsa_recoverable_deserialize(
            sigdecoded[0:64], sigdecoded[64])
        addr.pubkey.public_key = addr.pubkey.ecdsa_recover(
            bytearray([ord(c) for c in hrp]) + data.tobytes(), addr.signature)

    return addr


class LnAddr(object):
    def __init__(
            self,
            paymenthash=None,
            amount=None,
            currency='bc',
            tags=None,
            date=None):
        self.date = int(time.time()) if not date else int(date)
        self.tags = [] if not tags else tags
        self.unknown_tags = []
        self.paymenthash = paymenthash
        self.signature = None
        self.pubkey = None
        self.currency = currency
        self.amount = amount

    def __str__(self):
        return "LnAddr[{}, amount={}{} tags=[{}]]".format(
            hexlify(self.pubkey.serialize()).decode('utf-8'),
            self.amount, self.currency,
            ", ".join([k + '=' + str(v) for k, v in self.tags])
        )
