####################################################################################################
#
#  Copyright (C) 2022
#
####################################################################################################
#
#  project     : atmorep
#
#  author      : atmorep collaboration
# 
#  description :
#
#  license     :
#
####################################################################################################

import torch
import numpy as np
import math
import itertools
import code
# code.interact(local=locals())

import zarr
import pandas as pd

from atmorep.utils.utils import days_until_month_in_year
from atmorep.utils.utils import days_in_month

import atmorep.config.config as config

from atmorep.datasets.normalizer_global import NormalizerGlobal
from atmorep.datasets.normalizer_local import NormalizerLocal


class MultifieldDataSampler( torch.utils.data.IterableDataset):
    
  ###################################################
  def __init__( self, fields, levels, years, batch_size, pre_batch, n_size, num_samples_per_epoch,
                rng_seed = None, time_sampling = 1, with_source_idxs = False,
                fields_targets = None, pre_batch_targets = None ) :
    '''
      Data set for single dynamic field at an arbitrary number of vertical levels

      nsize : neighborhood in (tsteps, deg_lat, deg_lon)
    '''
    super( MultifieldDataSampler).__init__()

    self.fields = fields
    self.batch_size = batch_size
    self.n_size = n_size
    self.num_samples = num_samples_per_epoch
    self.with_source_idxs = with_source_idxs

    self.pre_batch = pre_batch

    # create (source) fields
    # config.path_data
    fname_source = '/p/scratch/atmo-rep/era5_res0025_1979.zarr'
    fname_source = '/p/scratch/atmo-rep/era5_res0025_2021.zarr'
    fname_source = '/p/scratch/atmo-rep/era5_res0025_2021_t5.zarr'
    # fname_source = '/p/scratch/atmo-rep/era5_res0100_2021_t5.zarr'
    self.ds = zarr.open( fname_source)
    self.ds_global = self.ds.attrs['is_global']
    self.ds_len = self.ds['data'].shape[0]

    # sanity checking
    # assert self.ds['data'].shape[0] == self.ds['time'].shape[0]
    # assert self.ds_len >= num_samples_per_epoch

    self.lats = np.array( self.ds['lats'])
    self.lons = np.array( self.ds['lons'])

    sh = self.ds['data'].shape
    st = self.ds['time'].shape
    print( f'self.ds[\'data\'] : {sh} :: {st}')
    print( f'self.lats : {self.lats.shape}', flush=True)
    print( f'self.lons : {self.lons.shape}', flush=True)

    self.fields_idxs = np.array( [self.ds.attrs['fields'].index( f[0]) for f in fields])
    self.levels_idxs = np.array( [self.ds.attrs['levels'].index( ll) for ll in levels])
    # self.fields_idxs = [0, 1, 2]
    # self.levels_idxs = [0, 1]
    self.levels = levels #[123, 137]  # self.ds['levels']

    # TODO
    # # create (target) fields 
    # self.datasets_targets = self.create_loaders( fields_targets)
    # self.fields_targets = fields_targets
    # self.pre_batch_targets = pre_batch_targets

    self.time_sampling = time_sampling
    self.range_lat = np.array( self.lats[ [0,-1] ])
    self.range_lon = np.array( self.lons[ [0,-1] ])

    self.res = np.zeros( 2)
    self.res[0] = (self.range_lat[1]-self.range_lat[0]) / (self.ds['data'].shape[-2]-1)
    self.res[1] = (self.range_lon[1]-self.range_lon[0]) / (self.ds['data'].shape[-1]-1)
    
    # ensure neighborhood does not exceed domain (either at pole or for finite domains)
    self.range_lat += np.array([n_size[1] / 2., -n_size[1] / 2.])
    # lon: no change for periodic case
    if self.ds_global < 1.:
      self.range_lon += np.array([n_size[2]/2., -n_size[2]/2.])

    # ensure all data loaders use same rng_seed and hence generate consistent data
    if not rng_seed :
      rng_seed = np.random.randint( 0, 100000, 1)[0]
    self.rng = np.random.default_rng( rng_seed)

    # data normalizers
    self.normalizers = []
    for _, field_info in enumerate(fields) :
      self.normalizers.append( [])
      corr_type = 'global' if len(field_info) <= 6 else field_info[6]
      ner = NormalizerGlobal if corr_type == 'global' else NormalizerLocal
      for vl in self.levels :
        self.normalizers[-1] += [ ner( field_info, vl, 
                                  np.array(self.ds['data'].shape)[[0,-2,-1]]) ]

    # extract indices for selected years
    self.times = pd.DatetimeIndex( self.ds['time'])
    # idxs = np.zeros( self.ds['time'].shape[0], dtype=np.bool_)
    # self.idxs_years = np.array( [])
    # for year in years :
    #   idxs = np.where( (self.times >= f'{year}-1-1') & (self.times <= f'{year}-12-31'))[0]
    #   assert idxs.shape[0] > 0, f'Requested year is not in dataset {fname_source}. Aborting.'
    #   self.idxs_years = np.append( self.idxs_years, idxs[::self.time_sampling])
    # TODO, TODO, TODO:
    self.idxs_years = np.arange( self.ds_len)

  ###################################################
  def shuffle( self) :

    rng = self.rng
    self.idxs_perm_t = rng.permutation( self.idxs_years)[:(self.num_samples // self.batch_size)]

    lats = rng.random(self.num_samples) * (self.range_lat[1] - self.range_lat[0]) +self.range_lat[0]
    lons = rng.random(self.num_samples) * (self.range_lon[1] - self.range_lon[0]) +self.range_lon[0]

    # align with grid
    res_inv = 1.0 / self.res * 1.00001
    lats = self.res[0] * np.round( lats * res_inv[0])
    lons = self.res[1] * np.round( lons * res_inv[1])

    self.idxs_perm = np.stack( [lats, lons], axis=1)

  ###################################################
  def __iter__(self):

    # TODO: if we keep this then we should remove the rng_seed argument for the constuctor
    self.rng = np.random.default_rng()
    self.shuffle()

    lats, lons = self.lats, self.lons
    fields_idxs, levels_idxs = self.fields_idxs, self.levels_idxs
    ts, n_size = self.time_sampling, self.n_size
    ns_2 = np.array(self.n_size) / 2.
    res = self.res

    iter_start, iter_end = self.worker_workset()

    for bidx in range( iter_start, iter_end) :
     
      idx = self.idxs_perm_t[bidx]
      idxs_t = list(np.arange( idx-n_size[0]*ts, idx, ts, dtype=np.int64))
      data_t = self.ds['data'].oindex[ idxs_t, fields_idxs , levels_idxs]

      sources, sources_infos, source_idxs = [], [], []
      for sidx in range(self.batch_size) :

        idx = self.idxs_perm[bidx*self.batch_size+sidx]

        # slight assymetry with offset by res/2 is required to match desired token count
        lat_ran = np.where(np.logical_and(lats > idx[0]-ns_2[1]-res[0]/2.,lats < idx[0]+ns_2[1]))[0]
        # handle periodicity of lon
        assert not ((idx[1]-ns_2[2]) < 0. and (idx[1]+ns_2[2]) > 360.)
        il, ir = (idx[1]-ns_2[2]-res[1]/2., idx[1]+ns_2[2])
        if il < 0. :
          lon_ran = np.concatenate( [np.where( lons > il+360)[0], np.where(lons < ir)[0]], 0)
        elif ir > 360. :
          lon_ran = np.concatenate( [np.where( lons > il)[0], np.where(lons < ir-360)[0]], 0)
        else : 
          lon_ran = np.where(np.logical_and( lons > il, lons < ir))[0]

        # extract data
        source = np.take( np.take( data_t, lat_ran, -2), lon_ran, -1)
        sources += [ np.expand_dims(source, 0) ]
        if self.with_source_idxs :
          source_idxs += [ (idxs_t, lat_ran, lon_ran) ]

        # normalize data
        # TODO: temporal window can span multiple months
        year, month = self.times[ idxs_t[-1] ].year, self.times[ idxs_t[-1] ].month
        for ifield, _ in enumerate(fields_idxs) :
          for ilevel, _ in enumerate(levels_idxs) :
            nf = self.normalizers[ifield][ilevel].normalize
            source[:,ifield,ilevel] = nf( year, month, source[:,ifield,ilevel], (lat_ran, lon_ran))

        # extract batch info
        sources_infos += [ [ self.ds['time'][ idxs_t ], self.levels, 
                             self.lats[lat_ran], self.lons[lon_ran], self.res ] ]

      # swap
      sources = self.pre_batch( torch.from_numpy( np.concatenate( sources, 0)), 
                                sources_infos )
      
      # TODO: implement targets
      target, target_info = None, None

      yield ( sources, (target, target_info), source_idxs )

  ###################################################
  def __len__(self):
      return self.num_samples // self.batch_size

  ###################################################
  def worker_workset( self) :

    worker_info = torch.utils.data.get_worker_info()

    if worker_info is None: 
      iter_start = 0
      iter_end = self.num_samples

    else:  
      # split workload
      per_worker = len(self) // worker_info.num_workers
      worker_id = worker_info.id
      iter_start = int(worker_id * per_worker)
      iter_end = int(iter_start + per_worker)
      if worker_info.id+1 == worker_info.num_workers :
        iter_end = len(self)

    return iter_start, iter_end

