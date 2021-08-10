import scanpy as sc
import anndata as ad
import squidpy as sq
from squidpy._constants._constants import CoordType


import numpy as np
from scipy.sparse import spmatrix
import pandas as pd

from PIL import Image
from matplotlib import colors
import matplotlib.pyplot as plt
from sklearn.cluster import KMeans

from scipy.spatial.distance import cdist
from scipy.sparse import csr_matrix
from numba import njit

from typing import List,Dict,Union,Optional
import numbers

from . import models as m
from . import utils as ut
from pathlib import Path

def get_landmark_distance(adata: ad.AnnData,
                          landmark_position_key: str = "curated_landmarks",
                          landmark_distance_key: str = "landmark_distances",
                          reference : Optional[Union[m.Reference,np.ndarray]] = None,
                          **kwargs,
                          )->None:

    assert "spatial" in adata.obsm,\
    "no coordinates for the data"

    assert landmark_position_key in adata.uns,\
    "landmarks not found in data"

    n_obs = adata.shape[0]
    n_landmarks = adata.uns[landmark_position_key].shape[0]

    distances = np.zeros((n_obs,n_landmarks))
    obs_crd = adata.obsm["spatial"].copy()
    lmk_crd = adata.uns["curated_landmarks"].copy()

    if isinstance(lmk_crd,pd.DataFrame):
        lmk_crd_names = list(lmk_crd.index)
        lmk_crd = lmk_crd.values
    else:
        lmk_crd_names = None

    if reference is not None:
        import morphops as mops
        if isinstance(reference,m.Reference):
            ref_lmk_crd = reference.landmarks.numpy()
            ref_lmk_crd_names = list(reference.lmk_to_pos.keys())
        if isinstance(reference,np.ndarray):
            ref_lmk_crd = reference
            ref_lmk_crd_names = None

        ref_lmk_crd,lmk_crd = ut.match_arrays_by_names(ref_lmk_crd,
                                                       lmk_crd,
                                                       ref_lmk_crd_names,
                                                       lmk_crd_names,
                                                       )

        obs_crd = mops.tps_warp(lmk_crd,ref_lmk_crd,obs_crd)
        lmk_crd = mops.tps_warp(lmk_crd,ref_lmk_crd,lmk_crd)

    for obs in range(n_obs):
        obs_x,obs_y = obs_crd[obs,:]
        for lmk in range(n_landmarks):
            lmk_x,lmk_y = lmk_crd[lmk,:]
            distances[obs,lmk] = ((obs_x - lmk_x)**2 + (obs_y-lmk_y)**2)**0.5

    adata.obsm[landmark_distance_key] = distances

def reference_to_grid(ref_img: Union[Image.Image,str],
                      n_approx_points: int = 1e4,
                      background_color:Union[str,Union[np.ndarray,tuple]] = "white",
                      n_regions: int = 2,
                      )->np.ndarray:

    from scipy.interpolate import griddata

    if isinstance(ref_img,str):
        ref_img_pth = Path(ref_img)
        if ref_img_pth.exists():
            ref_img = Image.open(ref_img_pth)
        else:
            raise FileNotFoundError(f"The file {ref_img_pth} cannot be found."\
            " Please enter a different image path.")

    w,h = ref_img.size
    new_w = 500
    w_ratio = new_w/w
    new_h = int(round(h * w_ratio))
    ref_img = (ref_img if ref_img.mode == "L" else ref_img.convert("RGBA"))
    img = ref_img.resize((new_w,new_h))
    img = np.asarray(img)
    if img.max() > 1:
        img = img / 255

    if len(img.shape) == 3:
        if isinstance(background_color,str):
            background_color = colors.to_rgba(background_color)
        elif isinstance(background_color,numbers.Number):
            background_color = np.array(background_color)
        else:
            raise ValueError(f"Color format {background_color} not supported.")

        km = KMeans(n_clusters = n_regions + 1,random_state=1)
        nw,nh,nc = img.shape
        idx = km.fit_predict(img.reshape(nw*nh,nc))
        centers = km.cluster_centers_[:,0:3]
        bg_id = np.argmin(np.linalg.norm(centers - background_color[0:3],axis=1))
        bg_row,bg_col = np.unravel_index(np.where(idx == bg_id),shape = (nw,nh))
        img = np.ones((nw,nh))
        img[bg_row,bg_col] = 0

        reg_img = np.ones(img.shape) * -1
        for clu in np.unique(idx):
            if clu != bg_id:
                reg_row,reg_col = np.unravel_index(np.where(idx == clu),shape = (nw,nh))
                reg_img[reg_row,reg_col] = clu

    elif len(img.shape) == 2:
        color_map = dict(black = 0,
                         white = 1,
                         )

        is_ref = img.round(0) == color_map[background_color]
        img = np.zeros((img.shape[0],img.shape[1]))
        img[is_ref] = 1
        img[~is_ref] = 0
        reg_img = np.ones(img.shape)
        reg_img[img == 0] = -1
    else:
        raise Exception("Wrong image format, must be grayscale or color")


    f_ref = img.sum() / (img.shape[0] * img.shape[1])
    f_ratio = img.shape[1] / img.shape[0]

    n_points = n_approx_points / f_ref

    size_x= np.sqrt(n_points / f_ratio)
    size_y = size_x * f_ratio

    xx = np.linspace(0,img.shape[0],int(round(size_x)))
    yy = np.linspace(0,img.shape[1],int(round(size_y)))

    xx,yy = np.meshgrid(xx,yy)
    crd = np.hstack((xx.flatten()[:,np.newaxis],
                     yy.flatten()[:,np.newaxis]))

    img_x = np.arange(img.shape[0])
    img_y = np.arange(img.shape[1])
    img_xx,img_yy = np.meshgrid(img_x,img_y)
    img_xx = img_xx.flatten()
    img_yy = img_yy.flatten()
    img_crd = np.hstack((img_xx[:,np.newaxis],img_yy[:,np.newaxis]))
    del img_xx,img_yy,img_x,img_y

    zz = griddata(img_crd,img.T.flatten(),(xx,yy))
    ww = griddata(img_crd,reg_img.T.flatten(),(xx,yy),method = "nearest")
    # crd = crd[zz.flatten() >= 0.5]
    crd = crd[ww.flatten() >= 0.0]
    crd = crd / w_ratio
    meta = ww.flatten()[ww.flatten() >= 0].round(0).astype(int)

    uni,mem = np.unique(meta,return_counts=True)
    srt = np.argsort(mem)[::-1]
    rordr = {old:new for new,old in enumerate(uni[srt])}
    meta = np.array([rordr[x] for x in meta])

    return crd[:,[1,0]],meta

def match_scales(adata: ad.AnnData,
                 reference: Union[np.ndarray,"m.Reference"],
                 )->None:

    n_lmk_thrs = 100

    obs_lmk = adata.uns["curated_landmarks"].copy()
    if isinstance(obs_lmk,pd.DataFrame):
        obs_lmk_names = list(obs_lmk.index)
        obs_lmk = obs_lmk.values
    else:
        obs_lmk_names = None

    if isinstance(reference,m.Reference):
        ref_lmk = reference.landmarks.detach().numpy()
        ref_lmk_names = list(reference.lmk_to_pos.keys())
    elif isinstance(reference,pd.DataFrame):
        ref_lmk = reference.values
        ref_lmk_names = list(reference.index)
    elif isinstance(reference,np.ndarray):
        ref_lmk = reference
        ref_lmk_names = None
    else:
        NotImplementedError("reference of type : {} is not supported".\
                            format(type(reference))
                            )

    ref_lmk,obs_lmk = ut.match_arrays_by_names(ref_lmk,
                                               obs_lmk,
                                               ref_lmk_names,
                                               obs_lmk_names,
                                               )

    n_lmk =  len(ref_lmk)
    n_use_lmk = min(n_lmk,n_lmk_thrs)

    lmk_idx = np.random.choice(n_lmk,
                               replace = False,
                               size = n_use_lmk)

    av_ratio = 0

    k = 0
    for i in range(n_use_lmk-1):
        for j in range(i+1,n_use_lmk):
            ii = lmk_idx[i]
            jj = lmk_idx[j]

            obs_d = ((obs_lmk[ii,0] - obs_lmk[jj,0])**2 + (obs_lmk[ii,1] - obs_lmk[jj,1])**2)**0.5
            ref_d = ((ref_lmk[ii,0] - ref_lmk[jj,0])**2 + (ref_lmk[ii,1] - ref_lmk[jj,1])**2)**0.5
            av_ratio += ref_d / obs_d
            k += 1

    av_ratio = av_ratio / k

    adata.obsm["spatial"] = adata.obsm["spatial"] * av_ratio
    adata.uns["curated_landmarks"] = adata.uns["curated_landmarks"] * av_ratio

    try:
        sample_name = list(adata.uns["spatial"].keys())[0]
        for scalef in ["tissue_hires_scalef","tissue_lowres_scalef"]:
            old_sf = adata.uns["spatial"][sample_name]["scalefactors"].get(scalef,1)
            adata.uns["spatial"][sample_name]["scalefactors"][scalef] = old_sf / av_ratio
    except:
        pass


def join_adatas(adatas:List[ad.AnnData],
                **kwargs,
                )->None:


    obs = np.array([0] + [a.shape[0] for a in adatas])
    features = pd.Index([])
    for a in adatas:
        features = features.union(a.var.index)

    n_features = len(features)
    starts = np.cumsum(obs).astype(int)
    n_obs = starts[-1]
    joint_matrix = pd.DataFrame(np.zeros((n_obs,n_features)),
                                columns = features,
                                )

    joint_obs = pd.DataFrame([])
    joint_obsm = {k:[] for k in adatas[0].obsm.keys()}

    for k,adata in enumerate(adatas):
        inter_features = features.intersection(adata.var.index)
        joint_matrix.loc[starts[k]:(starts[k+1]-1),inter_features] = adata.to_df().loc[:,inter_features].values
        tmp_obs = adata.obs.copy()
        tmp_obs["split_id"] = k
        joint_obs = pd.concat((joint_obs,
                              tmp_obs))

        for key in joint_obsm.keys():
            print(adatas[k].obsm[key].shape)
            joint_obsm[key].append(adatas[k].obsm[key])

    for key in joint_obsm.keys():
        joint_obsm[key] = np.concatenate(joint_obsm[key])


    var = pd.DataFrame(features.values,
                       index = features,
                       columns = ["features"],
                       )

    adata = ad.AnnData(joint_matrix,
                       obs = joint_obs,
                       var = var,
                       )

    adata.obsm = joint_obsm

    return adata

def spatial_smoothing(adata: ad.AnnData,
                      distance_key: str = "spatial",
                      n_neigh: int = 4,
                      coord_type: Union[str,CoordType] = "generic",
                      sigma: float = 50,
                      **kwargs,
                      )->None:

    spatial_key = kwargs.get("spatial_key","spatial")
    if spatial_key not in adata.obsm.keys():
        raise Exception("Spatial key not present in AnnData object")

    if distance_key not in adata.obsp.keys():
        sq.gr.spatial_neighbors(adata,
                                spatial_key = spatial_key,
                                coord_type=coord_type,
                                n_neigh=n_neigh,
                                key_added=distance_key,
                                **kwargs,
                                )
        distance_key = distance_key + "_distances"

    gr = adata.obsp[distance_key]
    n_obs,n_features = adata.shape
    new_X = np.zeros((n_obs,n_features))
    old_X = adata.X

    if isinstance(old_X,spmatrix):
        sp_type = type(old_X)
        old_X = np.array(old_X.todense())
    else:
        sp_type = None

    for obs in range(n_obs):
        ptr = slice(gr.indptr[obs],gr.indptr[obs+1])
        ind = gr.indices[ptr]

        ws = np.append(gr.data[ptr],0)
        ws = np.exp(-ws / sigma)
        ws /= ws.sum()
        ws = ws.reshape(-1,1)
        new_X[obs,:] = np.sum(old_X[np.append(ind,obs),:]*ws,axis=0)

    if sp_type is not None:
        new_X = sp_type(new_X)

    adata.layers["smoothed"] = new_X