"""Sampling diagnostics, not claims of population accuracy from a small pilot."""
from collections import defaultdict
import numpy as np
from scipy.stats import spearmanr

def navigation_stability(records,settings):
    grouped=defaultdict(list)
    for row in records:
        try:
            b,c,n=float(row['bus_time']),float(row['car_time']),float(row['transfer'])
            if not 0<b<=settings['bus_max'] or not 0<c<=settings['car_max']:continue
            grouped[row['grid_id']].append(c/(b+settings['alpha']*n))
        except (TypeError,KeyError,ValueError):continue
    levels=[];full={key:float(np.mean(values)) for key,values in grouped.items()}
    for k in range(1,max([len(v) for v in grouped.values()]+[0])+1):
        values={key:float(np.mean(sample[:k])) for key,sample in grouped.items() if len(sample)>=k}
        current=np.array(list(values.values()));reference=np.array([full[key] for key in values])
        correlation=float(spearmanr(current,reference).statistic) if len(current)>2 and np.ptp(current)>0 and np.ptp(reference)>0 else None
        levels.append({'destinations_per_origin':k,'eligible_origins':len(values),'mean_absolute_difference_from_available_full_sample':float(np.mean(np.abs(current-reference))) if len(current) else None,'spearman_rank_vs_available_full_sample':correlation})
    return {'levels':levels,'valid_origins':len(grouped),'valid_records':sum(map(len,grouped.values())),
            'limitations':['nested prefixes of the same fixed-seed sample, not independent validation','filtering can change eligible origins; no population confidence claim','at most a few destinations per origin; increase sample before concluding stability']}
