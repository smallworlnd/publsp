YEARLY_MINED_BLOCKS = int(24*60/10*365)


def calculate_lease_cost(
        fixed_cost: int,
        variable_cost_ppm: int,
        capacity: int,
        channel_expiry_blocks) -> int:
    """
    the LSP sets a yearly ppm for simplicity, and the customer can request any
    lease time (smaller than the LSPs max) so the yearly ppm on capacity needs
    to be pro-rated to the requested lease time
    """
    capacity_ppm = fixed_cost + variable_cost_ppm * 1e-6 * capacity
    prorated_period = channel_expiry_blocks / YEARLY_MINED_BLOCKS
    return round(capacity_ppm * prorated_period)


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
    max_lease_cost = calculate_lease_cost(
        fixed_cost=fixed_cost,
        variable_cost_ppm=variable_cost_ppm,
        capacity=capacity,
        channel_expiry_blocks=max_channel_expiry_blocks
    )
    num_yearly_renewals = YEARLY_MINED_BLOCKS / max_channel_expiry_blocks
    apr = max_lease_cost * num_yearly_renewals / capacity * 100
    return round(apr, 2)
