import math

DEFAULT_PARAMETERS = {
    'paths_per_transfer': 3,
    'access_station_count': 3,
    'max_walk_distance_m': 1000.0,
    'min_od_distance_km': 1.0,
    'max_od_distance_km': 20.0,
    'max_transfers': 2,
    'transfer_penalty_min': 2.0,
    'bus_speed_kmh': 18.0,
    'vehicle_capacity': 80.0,
    'max_iterations': 500,
    'relative_gap_tol': 1e-4,
}


def normalize_parameters(values=None):
    if values is not None and not isinstance(values, dict):
        raise ValueError('模型参数必须为对象')
    values = values or {}
    unknown = set(values) - set(DEFAULT_PARAMETERS)
    if unknown:
        raise ValueError('未知模型参数：' + ', '.join(sorted(unknown)))
    result = dict(DEFAULT_PARAMETERS)
    integer_fields = {'paths_per_transfer', 'access_station_count', 'max_transfers', 'max_iterations'}
    nonnegative_fields = {'min_od_distance_km', 'max_transfers', 'transfer_penalty_min'}
    for key, value in values.items():
        if isinstance(value, bool) or value is None:
            raise ValueError(f'{key} 必须为有效数字')
        number = float(value)
        if not math.isfinite(number) or number < 0 or (number == 0 and key not in nonnegative_fields):
            raise ValueError(f'{key} 取值无效')
        if key in integer_fields and not number.is_integer():
            raise ValueError(f'{key} 必须为整数')
        result[key] = int(number) if key in integer_fields else number
    if result['min_od_distance_km'] >= result['max_od_distance_km']:
        raise ValueError('OD最小距离必须小于最大距离')
    if result['relative_gap_tol'] >= 1:
        raise ValueError('间隙阈值必须大于0且小于1')
    return result
