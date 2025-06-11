YEARLY_MINED_BLOCKS = int(24*60/10*365)


def calculate_lease_cost(
        fixed_cost: int,
        variable_cost_ppm: int,
        capacity: int,
        channel_expiry_blocks: int,
        max_channel_expiry_blocks: int) -> int:
    """
    the LSP sets a yearly ppm for simplicity, and the customer can request any
    lease time (smaller than the LSPs max) so the yearly ppm on capacity needs
    to be pro-rated to the requested lease time
    """
    variable_cost = variable_cost_ppm * 1e-6 * capacity
    lease_time_factor = channel_expiry_blocks / max_channel_expiry_blocks
    return fixed_cost + round(variable_cost * lease_time_factor)


def calculate_apr(
        fixed_cost: int,
        variable_cost_ppm: int,
        capacity: int,
        max_channel_expiry_blocks: int) -> int:
    """
    assume the channel is closed after the LSP's max lease period and gets
    repurchased at the same price for the number of times the lease
    duration fits in a year
    """
    variable_cost = variable_cost_ppm * 1e-6 * capacity
    num_yearly_renewals = YEARLY_MINED_BLOCKS / max_channel_expiry_blocks
    apr = (fixed_cost + variable_cost) * num_yearly_renewals / capacity * 100
    return round(apr, 2)
