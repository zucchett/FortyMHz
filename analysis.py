#!/usr/bin/env python
# coding: utf-8

import os, sys, math
import pandas as pd
import numpy as np
import itertools
from datetime import datetime

from modules.mapping.config import TDRIFT, VDRIFT, DURATION, TIME_WINDOW, XCELL, ZCELL
from modules.mapping import *
from modules.analysis.patterns import PATTERNS, PATTERN_NAMES, ACCEPTANCE_CHANNELS, MEAN_TZERO_DIFF, MEANTIMER_ANGLES, meantimereq, mean_tzero, tzero_clusters
from modules.reco import config, plot

import argparse
parser = argparse.ArgumentParser(description='Command line arguments')
parser.add_argument("-d", "--display", action="store", type=int, default=0, dest="display", help="Number of event to display")
parser.add_argument("-e", "--event", action="store", type=int, default=0, dest="event", help="Inspect a signle event")
parser.add_argument("-i", "--inputfile", nargs='+', dest="filenames", default="data/Run000966/output_raw.dat", help="Provide input files (either binary or txt)")
parser.add_argument("-o", "--outputdir", action="store", type=str, dest="outputdir", default="./output/", help="Specify output directory")
parser.add_argument("-m", "--max", action="store", type=int, default=-1, dest="max", help="Maximum number of words to be read")
parser.add_argument("-x", "--meantimer", action="store_true", default=False, dest="meantimer", help="Force application of the meantimer algorithm (override BX assignment)")
parser.add_argument("-v", "--verbose", action="store_true", default=False, dest="verbose", help="Increase verbosity")
args = parser.parse_args()

runname = [x for x in args.filenames[0].split('/') if 'Run' in x][0] if "Run" in args.filenames[0] else "Run000000"

if not os.path.exists(args.outputdir): os.makedirs(args.outputdir)
for d in ["plots", "display", "csv"]:
    if not os.path.exists(args.outputdir + runname + "_" + d + "/"): os.makedirs(args.outputdir + runname + "_" + d + "/")

# Copy data from LNL to CERN
# scp /data/Run001089/*.dat zucchett@lxplus.cern.ch:/afs/cern.ch/work/z/zucchett/public/FortyMHz/Run001089/
# Copy data from CERN to local
# mkdir data/Run001089
# scp lxplus.cern.ch:/afs/cern.ch/work/z/zucchett/public/FortyMHz/Run001089/* data/Run001089/


# Layer    # Parameters

#          +--+--+--+--+--+--+--+--+
# 1        |  1  |  5  |  9  |  13 | 17 ...
#          +--+--+--+--+--+--+--+--+
# 2           |  3  |  7  |  11 | 15 ...
#          +--+--+--+--+--+--+--+--+
# 3        |  2  |  6  |  10 |  14 | 18 ...
#          +--+--+--+--+--+--+--+--+
# 4           |  4  |  8  |  12 | 16 ...
#          +--+--+--+--+--+--+--+--+


def meantimer(adf):
    adf = adf.drop_duplicates() 
    tzeros, angles = meantimer_results(adf)
    adf['T0'] = np.mean(tzeros) if len(tzeros) > 0 else np.nan
    '''
    df = df.loc[df['SL']==sl+1]
    adf = df.drop_duplicates()
    adf = adf.loc[(adf['TDC_CHANNEL']!=139)]
    if adf.shape[0] < CHAN_PER_DF:
    out[event+sl/10.] = []
    return out[event+sl/10.]
    tzeros = meantimer_results(adf)[0]
    out[event+sl/10.] = tzeros
    return out[event+sl/10.]
    '''
    return adf

#from numba import jit
#@jit
def meantimer_results(df_hits, verbose=False):
    """Run meantimer over the group of hits"""
    sl = df_hits['SL'].iloc[0]
    # Getting a TIME column as a Series with TDC_CHANNEL_NORM as index
    df_time = df_hits.loc[:, ['TDC_CHANNEL_NORM', 'TIME_ABS', 'LAYER']]
    df_time.sort_values('TIME_ABS', inplace=True)
    # Split hits in groups where time difference is larger than maximum event duration
    grp = df_time['TIME_ABS'].diff().fillna(0)
    event_width_max = 1.1*TDRIFT
    grp[grp <= event_width_max] = 0
    grp[grp > 0] = 1
    grp = grp.cumsum().astype(np.uint16)
    df_time['grp'] = grp
    # Removing groups with less than 3 unique hits
    df_time = df_time[df_time.groupby('grp')['TDC_CHANNEL_NORM'].transform('nunique') >= 3]
    # Determining the TIME0 using triplets [no external trigger]
    tzeros = []
    angles = []
    # Processing each group of hits
    patterns = PATTERN_NAMES.keys()
    for grp, df_grp in df_time.groupby('grp'):
        df_grp.set_index('TDC_CHANNEL_NORM', inplace=True)
        # Selecting only triplets present among physically meaningful hit patterns
        channels = set(df_grp.index.astype(np.int16))
        triplets = set(itertools.permutations(channels, 3))
        triplets = triplets.intersection(patterns)
        # Grouping hits by the channel for quick triplet retrieval
        times = df_grp.groupby(df_grp.index)['TIME_ABS']
        # Analysing each triplet
        for triplet in triplets:
            triplet_times = [times.get_group(ch).values for ch in triplet]
            for t1 in triplet_times[0]:
                for t2 in triplet_times[1]:
                    for t3 in triplet_times[2]:
                        timetriplet = (t1, t2, t3)
                        if max(timetriplet) - min(timetriplet) > 1.1*TDRIFT:
                            continue
                        pattern = PATTERN_NAMES[triplet]
                        mean_time, angle = meantimereq(pattern, timetriplet)
                        if verbose:
                            print('{4:d} {0:s}: {1:.0f}  {2:+.2f}  {3}'.format(pattern, mean_time, angle, triplet, sl))
                        # print(triplet, pattern, mean_time, angle)
                        #if not MEANTIMER_ANGLES[sl][0] < angle < MEANTIMER_ANGLES[sl][1]: # Override requirement as long as SL are swapped
                        if not -0.3 < angle < 0.3:
                            continue
                        tzeros.append(mean_time)
                        angles.append(angle)

    return tzeros, angles


### -------------------------------------------

def recoSegments(hitlist):
    global segments
    iorbit, isl = hitlist.name
    nhits = len(hitlist)
    if nhits < 3 or nhits > 20:
        if args.verbose: print("Skipping event", iorbit, ", chamber", isl, ", exceeds the maximum/minimum number of hits (", nhits, ")")
        return
    # Explicitly introduce left/right ambiguity
    lhits, rhits = hitlist.copy(), hitlist.copy()
    lhits['X_LABEL'] = 1
    lhits['X'] = lhits['X_LEFT']
    rhits['X_LABEL'] = 2
    rhits['X'] = rhits['X_RIGHT']
    lrhits = lhits.append(rhits, ignore_index=True) # Join the left and right hits
    
    # Compute all possible combinations in the most efficient way
    layer_list = [list(lrhits[lrhits['LAYER'] == x + 1].index) for x in range(min(nhits, 4))]
    all_combs = list(itertools.product(*layer_list))
    if args.verbose: print("Reconstructing event", iorbit, ", chamber", isl, ", has", nhits, "hits ->", len(all_combs), "combinations")
    fitRange, fitResults = (hitlist['Z'].min() - 0.5*ZCELL, hitlist['Z'].max() + 0.5*ZCELL), []
    
    # Fitting each combination
    for comb in all_combs:
        lrcomb = lrhits.iloc[list(comb)]
        # Try to reject improbable combinations: difference between adjacent wires should be 2 or smaller
        if len(lrcomb) >= 3 and max(abs(np.diff(lrcomb['WIRE'].astype(np.int16)))) > 2: continue
        posx, posz = lrcomb['X'], lrcomb['Z']
        seg_layer, seg_wire, seg_bx, seg_label = lrcomb['LAYER'].values, lrcomb['WIRE'].values, lrcomb['BX'].values, lrcomb['X_LABEL'].values
        
        # Fit
        pfit, residuals, rank, singular_values, rcond = np.polyfit(posx, posz, 1, full=True)
        if len(residuals) > 0:
            p1, p0 = pfit
            chi2 = residuals[0] / max(nhits, 4)
            if chi2 < 10. and abs(p1) > 1.0: #config.FIT_CHI2_MAX:
                fitResults.append({"chi2" : chi2, "label" : seg_label, "layer" : seg_layer, "wire" : seg_wire, "bx" : seg_bx, "pars" : [p0, p1]})
        
    fitResults.sort(key=lambda x: x["chi2"])

    if len(fitResults) > 0:
        segments = segments.append(pd.DataFrame.from_dict({'VIEW' : ['0'], 'ORBIT' : [iorbit], 'CHAMBER' : [[isl]], 'NHITS' : [nhits], 'P0' : [fitResults[0]["pars"][0]], "P1" : [fitResults[0]["pars"][1]], "CHI2" : [fitResults[0]["chi2"]]}), ignore_index=True)
        for ilabel, ilayer, iwire, ibx in zip(fitResults[0]["label"], fitResults[0]["layer"], fitResults[0]["wire"], fitResults[0]["bx"]):
            x_fit = (lrhits.loc[(lrhits['BX'] == ibx) & (lrhits['LAYER'] == ilayer) & (lrhits['WIRE'] == iwire) & (lrhits['X_LABEL'] == ilabel), 'Z'].values[0] - fitResults[0]["pars"][0]) / fitResults[0]["pars"][1]
            hitlist.loc[(hitlist['ORBIT'] == iorbit) & (hitlist['BX'] == ibx) & (hitlist['CHAMBER'] == isl) & (hitlist['LAYER'] == ilayer) & (hitlist['WIRE'] == iwire), ['X_LABEL', 'X_FIT']] = ilabel, x_fit

    return hitlist

### -------------------------------------------

def recoTracks(hitlist):
    global segments
    iorbit = hitlist.name
    
    # Loop on the views (xz, yz)
    for view, sls in GLOBAL_VIEW_SLs.items():
        sl_ids = [sl.id for sl in sls]
        viewhits = hitlist[(hitlist['CHAMBER'].isin(sl_ids)) & (hitlist['X'].notnull())]
        nhits = len(viewhits)
        if nhits < 3: continue
        posxy, posz = viewhits[view[0].upper() + '_GLOB'], viewhits[view[1].upper() + '_GLOB']
        # Fit
        pfit, residuals, rank, singular_values, rcond = np.polyfit(posxy, posz, 1, full=True)
        if len(residuals) > 0:
            p1, p0 = pfit
            chi2 = residuals[0] / nhits
            if chi2 < 10. and abs(p1) > 1.0: #config.FIT_CHI2_MAX:
                segments = segments.append(pd.DataFrame.from_dict({'VIEW' : [view.upper()], 'ORBIT' : [iorbit], 'CHAMBER' : [sl_ids], 'NHITS' : [nhits], 'P0' : [p0], "P1" : [p1], "CHI2" : [chi2]}), ignore_index=True)
    
    return hitlist

### -------------------------------------------


itime = datetime.now()
if args.verbose: print("Starting script [", itime, "]")

### Open file ###

if args.verbose: print("Importing dataset...")

df = pd.DataFrame()

for filename in args.filenames:

    if filename.endswith('.dat'):
        from modules.unpacker import *
        unpk = Unpacker()
        inputFile = open(filename, 'rb')
        dt = unpk.unpack(inputFile, args.max)

        if args.verbose: print("Read %d lines from binary file %s" % (len(dt), filename))
        df = df.append(pd.DataFrame.from_dict(dt), ignore_index=True)
        
    elif filename.endswith('.txt') or filename.endswith('.csv'):
        # force 7 fields
        # in case of forcing, for some reason, the first raw is not interpreted as columns names
        dt = pd.read_csv(filename, \
            names=['HEAD', 'FPGA', 'TDC_CHANNEL', 'ORBIT_CNT', 'BX_COUNTER', 'TDC_MEAS', 'TRG_QUALITY'], \
            dtype={'HEAD' : 'int32', 'FPGA' : 'int32', 'TDC_CHANNEL' : 'int32', 'ORBIT_CNT' : 'int32', 'BX_COUNTER' : 'int32', 'TDC_MEAS' : 'int32', 'TRG_QUALITY' : 'float64'}, \
            low_memory=False, \
            skiprows=1, \
            nrows=args.max*1024 + 1 if args.max > 0 else 1e9, \
        )
        if args.verbose: print("Read %d lines from txt file %s" % (len(dt), filename))
        df = df.append(dt, ignore_index=True)

    else:
        print("File format not recognized, skipping file...")

if args.verbose: print(df.head(50))

# remove tdc_channel = 139 since they are not physical events
df = df[df['TDC_CHANNEL']<136]
if args.event > 0: df = df[df['ORBIT_CNT'] == args.event]

#df = df[df['FPGA']==0]
#df = df[df['ORBIT_CNT'] == 544830352] # monstre event
#df = df[df['ORBIT_CNT'] == 1406379098] # another monstre events
#df = df[df['ORBIT_CNT'] == 1406809648] # super-monstre event
#df = df[df['ORBIT_CNT'] == 1406978288] # another super-monstre event
#df = df[df['ORBIT_CNT'] == 1407759916] # Mega-monstre
#df = df[df['ORBIT_CNT'] == 1412153032] # Iper-monstre
#df = df[df['ORBIT_CNT'] == 544654370]
#df = df[df['ORBIT_CNT'] == 544768455]


# remove double hits
##df['TDC_MEAS'] = df.groupby(['HEAD', 'FPGA', 'ORBIT_CNT', 'TDC_CHANNEL'])['TDC_MEAS'].transform(np.max)
##df = df.drop_duplicates(subset=['HEAD', 'FPGA', 'ORBIT_CNT', 'TDC_CHANNEL'], keep='last')
#df = df.drop_duplicates(subset=['HEAD', 'FPGA', 'ORBIT_CNT', 'TDC_CHANNEL'], keep='first')

if len(df) == 0:
    print("Empty dataframe, exiting...")
    exit()

if args.verbose: print("Mapping channels...")

# Map TDC_CHANNEL, FPGA to SL, LAYER, WIRE_NUM, WIRE_POS
mapconverter = Mapping()
df = mapconverter.virtex7(df)
#df = mapconverter.virtex7obdt(df)


# FIXME: SL 0 and 1 are swapped
pd.options.mode.chained_assignment = None
df.loc[df['SL'] == 0, 'SL'] = -1
df.loc[df['SL'] == 1, 'SL'] = 0
df.loc[df['SL'] == -1, 'SL'] = 1

if args.verbose: print("Determining BX0...")


# Determine BX0 either using meantimer or the trigger BX assignment
if args.meantimer: # still to be developed
    mitime = datetime.now()

    # Use only hits
    df = df[df['HEAD']==1]
    
    # Add necessary columns
    #df['TDC_CHANNEL_NORM'] = (df['TDC_CHANNEL'] - 64 * (df['SL']%2)).astype(np.uint8)
    df['TIME_ABS'] = (df['ORBIT_CNT'].astype(np.float64)*DURATION['orbit'] + df['BX_COUNTER'].astype(np.float64)*DURATION['bx'] + df['TDC_MEAS'].astype(np.float64)*DURATION['tdc']).astype(np.float64)

    
    if args.verbose: print("+ Running meantimer...")
    # Group by orbit counter (event) and SL    
    df = df.groupby(['ORBIT_CNT', 'SL'], as_index=False).apply(meantimer)
    
    '''
    grpbyorbit = df.groupby(['ORBIT_CNT', 'SL'])
    norbit, norbits = 0, df['ORBIT_CNT'].nunique()
    for (iorbit, isl), adf in grpbyorbit:
        norbit += 1
        if args.verbose and norbit % 100 == 0:
            print " + Running meantimer... (%3.2f %%)\r" % (100.*float(norbit)/float(norbits)),
            sys.stdout.flush()
        adf = adf.drop_duplicates() 
        tzeros = meantimer_results(adf)[0]
        if len(tzeros) > 0: df.loc[(df['ORBIT_CNT'] == iorbit) & (df['SL'] == isl), 'T0'] = np.mean(tzeros)
    '''

    mftime = datetime.now()
    if args.verbose: print("\nMeantimer completed [", mftime - mitime, "]")
    
    # Calculate drift time
    hits = df[df['T0'].notna()].copy()
    hits['TDRIFT'] = (hits['TIME_ABS'] - hits['T0']).astype(np.float32)

else:
    # Take the minimum BX selected among the macro-cells, and propagate it to the other rows in the same orbit
    df['T0'] = df[df['HEAD']==3].groupby('ORBIT_CNT')['TDC_MEAS'].transform(np.min)
    df['T0'] = df.groupby('ORBIT_CNT')['T0'].transform(np.max)

    # Select only valid hits
    sparhits = df.loc[df['T0'].isnull()].copy()
    trighits = df.loc[df['T0'] >= 0].copy()
    hits = trighits.loc[df['HEAD']==1].copy()

    # Create column TDRIFT
    hits['TDRIFT'] = (hits['BX_COUNTER']-hits['T0'])*DURATION['bx'] + hits['TDC_MEAS']*DURATION['tdc']

if args.verbose: print("Assigning positions...")

# Find events
hits = hits[(hits['TDRIFT']>TIME_WINDOW[0]) & (hits['TDRIFT']<TIME_WINDOW[1])]

# Count hits in each event
hits['NHITS'] = hits.groupby('ORBIT_CNT')['TDC_CHANNEL'].transform(np.size)

# Conversion from time to position
mapconverter.addXleftright(hits)

# Cosmetic changes to be compliant with common format
hits.rename(columns={'ORBIT_CNT': 'ORBIT', 'BX_COUNTER': 'BX', 'SL' : 'CHAMBER', 'WIRE_NUM' : 'WIRE', 'Z_POS' : 'Z', 'TDRIFT' : 'TIMENS'}, inplace=True)

if args.verbose: print(hits[hits['TDC_CHANNEL'] >= -128].head(50))

utime = datetime.now()
if args.verbose: print("Unpacking completed [", utime, "],", "time elapsed [", utime - itime, "]")


# Reconstruction
events = hits[['ORBIT', 'BX', 'NHITS', 'CHAMBER', 'LAYER', 'WIRE', 'X_LEFT', 'X_RIGHT', 'Z', 'TIMENS', 'TDC_MEAS', 'T0']]

events[['X', 'X_FIT']] = [np.nan, np.nan]
events['X_LABEL'] = 0
events['Y'] = 0.
events[['X_GLOB', 'Y_GLOB', 'Z_GLOB']] = [np.nan, np.nan, np.nan]

segments = pd.DataFrame()


# Reconstruction
from modules.geometry.sl import SL
from modules.geometry.segment import Segment
from modules.geometry import Geometry, COOR_ID
from modules.analysis import config as CONFIGURATION

# Initialize geometry
G = Geometry(CONFIGURATION)
SLs = {}
for iSL in config.SL_SHIFT.keys():
    SLs[iSL] = SL(iSL, config.SL_SHIFT[iSL], config.SL_ROTATION[iSL])

# Defining which SLs should be plotted in which global view
GLOBAL_VIEW_SLs = {
    'xz': [SLs[0], SLs[2]],
    'yz': [SLs[1], SLs[3]]
}

events = events.groupby(['ORBIT', 'CHAMBER'], as_index=False).apply(recoSegments)


'''
evs = events.groupby(['ORBIT', 'CHAMBER'])
ievs, nevs = 0., len(evs)

# Loop on events (same orbit)
for ievsl, hitlist in evs:
    ievs += 1.
    iorbit, isl = ievsl
    nhits = len(hitlist)

    # Global coordinates
    
    # Calculating the hit positions for each SL in the global reference frame
    for iSL, sl in SLs.items():
        slmask = hitlist['CHAMBER'] == iSL
        # Updating global positions for left hits
        pos_global = sl.coor_to_global(hitlist.loc[slmask, ['X', 'Y', 'Z']].values)
        hitlist.loc[slmask, ['X_GLOB', 'Y_GLOB', 'Z_GLOB']] = pos_global
    print(hitlist)


    if nhits < 3 or nhits > 20:
        if args.verbose: print("Skipping event", iorbit, ", chamber", isl, ", exceeds the maximum/minimum number of hits (", nhits, ")")
        continue
    # Explicitly introduce left/right ambiguity
    lhits, rhits = hitlist.copy(), hitlist.copy()
    lhits['X_LABEL'] = 1
    lhits['X'] = lhits['X_LEFT']
    rhits['X_LABEL'] = 2
    rhits['X'] = rhits['X_RIGHT']
    lrhits = lhits.append(rhits, ignore_index=True) # Join the left and right hits
    
    # Compute all possible combinations in the most efficient way
    layer_list = [list(lrhits[lrhits['LAYER'] == x + 1].index) for x in range(min(nhits, 4))]
    all_combs = list(itertools.product(*layer_list))
    if args.verbose: print("Reconstructing event", iorbit, ", chamber", isl, ", has", nhits, "hits ->", len(all_combs), "combinations [%.2f %%]" % (100.*ievs/nevs))
    fitRange, fitResults = (hitlist['Z'].min() - 0.5*ZCELL, hitlist['Z'].max() + 0.5*ZCELL), []
    
    # Fitting each combination
    for comb in all_combs:
        lrcomb = lrhits.iloc[list(comb)]
        # Try to reject improbable combinations: difference between adjacent wires should be 2 or smaller
        if len(lrcomb) >= 3 and max(abs(np.diff(lrcomb['WIRE'].astype(np.int16)))) > 2: continue
        posx, posz = lrcomb['X'], lrcomb['Z']
        seg_layer, seg_wire, seg_bx, seg_label = lrcomb['LAYER'].values, lrcomb['WIRE'].values, lrcomb['BX'].values, lrcomb['X_LABEL'].values
        
        # Fit
        
        #pfit, stats = Polynomial.fit(posx, posz, 1, full=True, window=fitRange, domain=fitRange)
        #if len(stats[0]) > 0:
        #    chi2 = stats[0][0] / max(nhits, 4)
        #    p0, p1 = pfit
        #    if chi2 < 10. and abs(p1) > 1.0: #config.FIT_CHI2_MAX:
        #        fitResults.append({"chi2" : chi2, "label" : seg_label, "layer" : seg_layer, "wire" : seg_wire, "pars" : [p0, p1]})
        
        pfit, residuals, rank, singular_values, rcond = np.polyfit(posx, posz, 1, full=True)
        if len(residuals) > 0:
            p1, p0 = pfit
            chi2 = residuals[0] / max(nhits, 4)
            if chi2 < 10. and abs(p1) > 1.0: #config.FIT_CHI2_MAX:
                fitResults.append({"chi2" : chi2, "label" : seg_label, "layer" : seg_layer, "wire" : seg_wire, "bx" : seg_bx, "pars" : [p0, p1]})
        
    fitResults.sort(key=lambda x: x["chi2"])

    if len(fitResults) > 0:
        segments = segments.append(pd.DataFrame.from_dict({'ORBIT' : [iorbit], 'CHAMBER' : [isl], 'NHITS' : [len(hitlist)], 'P0' : [fitResults[0]["pars"][0]], "P1" : [fitResults[0]["pars"][1]], "CHI2" : [fitResults[0]["chi2"]]}), ignore_index=True)
        for ilabel, ilayer, iwire, ibx in zip(fitResults[0]["label"], fitResults[0]["layer"], fitResults[0]["wire"], fitResults[0]["bx"]):
            x_fit = (lrhits.loc[(lrhits['BX'] == ibx) & (lrhits['LAYER'] == ilayer) & (lrhits['WIRE'] == iwire) & (lrhits['X_LABEL'] == ilabel), 'Z'].values[0] - fitResults[0]["pars"][0]) / fitResults[0]["pars"][1]
            events.loc[(events['ORBIT'] == iorbit) & (events['BX'] == ibx) & (events['CHAMBER'] == isl) & (events['LAYER'] == ilayer) & (events['WIRE'] == iwire), ['X_LABEL', 'X_FIT']] = ilabel, x_fit
'''
events.loc[events['X_LABEL'] == 1, 'X'] = events['X_LEFT']
events.loc[events['X_LABEL'] == 2, 'X'] = events['X_RIGHT']

if args.verbose: print("Adding global positions [", datetime.now(), "],", "time elapsed [", datetime.now() - itime, "]")

# Updating global positions
for iSL, sl in SLs.items():
    slmask = events['CHAMBER'] == iSL
    events.loc[slmask, ['X_GLOB', 'Y_GLOB', 'Z_GLOB']] = sl.coor_to_global(events.loc[slmask, ['X', 'Y', 'Z']].values)

if args.verbose: print("Reconstructing tracks [", datetime.now(), "],", "time elapsed [", datetime.now() - itime, "]")

events = events.groupby('ORBIT', as_index=False).apply(recoTracks)

rtime = datetime.now()
if args.verbose: print("Reconstruction completed [", rtime, "],", "time elapsed [", rtime - itime, "]")

if args.verbose:
    print(events.head(50))
    print(segments.head(10))
    print(segments.tail(10))

# Output to csv files
events.to_csv(args.outputdir + runname + "_csv/events.csv", header=True, index=False)
segments.to_csv(args.outputdir + runname + "_csv/segments.csv", header=True, index=False)



# Event display
import bokeh

evs = events.groupby(['ORBIT'])

ndisplays = 0

# Loop on events (same orbit)
for orbit, hitlist in evs:
    ndisplays += 1
    if ndisplays > args.display: break
    if args.verbose: print("Drawing event", orbit, "...")
    # Creating figures of the chambers
    figs = {}
    figs['sl'] = plot.book_chambers_figure(G)
    figs['global'] = plot.book_global_figure(G, GLOBAL_VIEW_SLs)
    # Draw chamber
    for iSL, sl in SLs.items():
        # Hits
        hitsl = hitlist[hitlist['CHAMBER'] == iSL]
        figs['sl'][iSL].circle(x=hitsl['X_LEFT'].values, y=hitsl['Z'], size=5, fill_color='black', fill_alpha=0.5, line_width=0)
        figs['sl'][iSL].circle(x=hitsl['X_RIGHT'].values, y=hitsl['Z'], size=5, fill_color='black', fill_alpha=0.5, line_width=0)
        figs['sl'][iSL].circle(x=hitsl['X'].values, y=hitsl['Z'], size=5, fill_color='black', fill_alpha=1., line_width=0)
        # Segments
        if len(segments) <= 0: continue
        segsl = segments[(segments['VIEW'] == '0') & (segments['ORBIT'] == orbit) & (segments['CHAMBER'] == iSL)]
        for index, seg in segsl.iterrows():
            #col = config.TRACK_COLORS[iR]
            segz = [G.SL_FRAME['b']+1, G.SL_FRAME['t']-1]
            segx = [((z - seg['P0']) / seg['P1']) for z in segz]
            #print(segz, segx, seg['P1'], seg['P0'])
            figs['sl'][iSL].line(x=np.array(segx), y=np.array(segz), line_color='black', line_alpha=0.7, line_width=3)

    # Global points
    for view, sls in GLOBAL_VIEW_SLs.items():
        sl_ids = [sl.id for sl in sls]
        viewhits = hitlist.loc[hitlist['CHAMBER'].isin(sl_ids)]
        figs['global'][view].circle(x=viewhits[view[0].upper() + '_GLOB'], y=viewhits[view[1].upper() + '_GLOB'], fill_color='black', fill_alpha=1., line_width=0)
    
        # Segments
        if len(segments) <= 0: continue
        tracks = segments[(segments['VIEW'] == view.upper()) & (segments['ORBIT'] == orbit)]
        for index, trk in tracks.iterrows():
            trkz = [plot.PLOT_RANGE['y'][0] + 1, plot.PLOT_RANGE['y'][1] - 1]
            trkxy = [((z - trk['P0']) / trk['P1']) for z in trkz]
            figs['global'][view].line(x=np.array(trkxy), y=np.array(trkz), line_color='black', line_alpha=0.7, line_width=3)


    plots = [[figs['sl'][l]] for l in [3, 2, 1, 0]]
    plots.append([figs['global'][v] for v in ['xz', 'yz']])
    bokeh.io.output_file(args.outputdir + runname + "_display/orbit_%d.html" % orbit, mode='cdn')
    bokeh.io.save(bokeh.layouts.layout(plots))



# General plots

if args.verbose: print("Producing general plots...")

import matplotlib.pyplot as plt

if args.verbose: print("Writing plots in directory %s" % (args.outputdir + runname))

# Timebox
plt.figure(figsize=(15,10))
for chamber in range(4):
    hist, bins = np.histogram(hits.loc[hits['CHAMBER']==chamber, 'TIMENS'], density=False, bins=80, range=(-150, 650))
    plt.subplot(2, 2, chamber+1)
    plt.title("Timebox [chamber %d]" % chamber)
    plt.xlabel("Time (ns)")
    plt.bar((bins[:-1] + bins[1:]) / 2, hist, align='center', width=np.diff(bins))
plt.savefig(args.outputdir + runname + "_plots/timebox.png")
plt.savefig(args.outputdir + runname + "_plots/timebox.pdf")


# Space boxes
plt.figure(figsize=(15,10))
for chamber in range(4):
    hist, bins = np.histogram(hits.loc[hits['CHAMBER']==chamber, 'X_LEFT'], density=False, bins=70, range=(-5, 30))
    plt.subplot(2, 2, chamber+1)
    plt.title("Space box LEFT [chamber %d]" % chamber)
    plt.xlabel("Position (mm)")
    plt.bar((bins[:-1] + bins[1:]) / 2, hist, align='center', width=np.diff(bins))
plt.savefig(args.outputdir + runname + "_plots/spacebox_left.png")
plt.savefig(args.outputdir + runname + "_plots/spacebox_left.pdf")

plt.figure(figsize=(15,10))
for chamber in range(4):
    hist, bins = np.histogram(hits.loc[hits['CHAMBER']==chamber, 'X_RIGHT'], density=False, bins=70, range=(-5, 30))
    plt.subplot(2, 2, chamber+1)
    plt.title("Space box RIGHT [chamber %d]" % chamber)
    plt.xlabel("Position (mm)")
    plt.bar((bins[:-1] + bins[1:]) / 2, hist, align='center', width=np.diff(bins))
plt.savefig(args.outputdir + runname + "_plots/spacebox_right.png")
plt.savefig(args.outputdir + runname + "_plots/spacebox_right.pdf")


# Occupancy
plt.figure(figsize=(15,20))
occupancy = hits.groupby(["CHAMBER", "LAYER", "WIRE"])['HEAD'].count() # count performed a random column
occupancy = occupancy.reset_index().rename(columns={'HEAD' : 'COUNTS'}) # reset the indices and make new ones, because the old indices are needed for selection
for chamber in range(4):
    for layer in range(4):
        x = np.array( occupancy.loc[((occupancy['CHAMBER'] == chamber) & (occupancy['LAYER'] == layer+1)), 'WIRE'] )
        y = np.array( occupancy.loc[((occupancy['CHAMBER'] == chamber) & (occupancy['LAYER'] == layer+1)), 'COUNTS'] )
        plt.subplot(4, 4, chamber*4 + layer + 1)
        plt.title("Occupancy [SL %d, LAYER %d]" % (chamber, layer+1))
        plt.xlabel("Wire number")
        plt.bar(x, y)
plt.savefig(args.outputdir + runname + "_plots/occupancy.png")
plt.savefig(args.outputdir + runname + "_plots/occupancy.pdf")


# Multiplicity
plt.figure(figsize=(15,10))
for chamber in range(4):
    plt.subplot(2, 2, chamber + 1)
    plt.title("Multiplicity [SL %d]" % (chamber, ))
    #plt.hist( hits.groupby(['ORBIT', 'CHAMBER'])['TDC_CHANNEL'].transform(np.size), bins=range(40), density=False )
    plt.hist( hits[(hits['CHAMBER'] == chamber)].groupby('ORBIT')['TDC_CHANNEL'].count(), bins=range(40), density=False )
    plt.xlabel("Number of hits")
plt.savefig(args.outputdir + runname + "_plots/multiplicity.png")
plt.savefig(args.outputdir + runname + "_plots/multiplicity.pdf")


# Chi2
plt.figure(figsize=(15,10))
for chamber in range(4):
    hist, bins = np.histogram(chi2s[chamber], density=False, bins=20, range=(0, 2))
    plt.subplot(2, 2, chamber+1)
    plt.title("Chi2 [chamber %d]" % chamber)
    plt.xlabel("Chi2")
    plt.bar((bins[:-1] + bins[1:]) / 2, hist, align='center', width=np.diff(bins))
plt.savefig(options.outputdir + runname + "_plots/chi2.png")
plt.savefig(options.outputdir + runname + "_plots/chi2.pdf")


if args.verbose: print("Done.")


# python analysis.py -i data/Run000966/output_raw.dat -m 1 -v
