from typing import List
from publsp.ln.base import Utxo


def spend_all_cost(inputs: List[Utxo], chain_fee_sat_vb: float, num_outputs: int = 2) -> int:
    header = 10.5
    output_cost = 31 * num_outputs
    sum_utxos_cost = sum([utxo.spend_cost_vb for utxo in inputs])
    return round((header + output_cost + sum_utxos_cost) * chain_fee_sat_vb)
