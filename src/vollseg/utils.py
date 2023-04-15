#!/usr/bin/env python3
"""
Created on Fri Sep 27 13:08:41 2019
@author: vkapoor
"""


import concurrent
import glob
import math
import os
from pathlib import Path
import torch
import napari
import gc
import time as cputime
from skimage.transform import resize

# import matplotlib.pyplot as plt
import numpy as np
import pandas as pd
from cellpose_vollseg import models
from csbdeep.utils import normalize
from qtpy.QtWidgets import QComboBox, QPushButton
from scipy import spatial
from scipy.ndimage import (
    binary_dilation,
    binary_erosion,
    distance_transform_edt,
)
from scipy.ndimage.measurements import find_objects
from scipy.ndimage.morphology import binary_fill_holes
from skimage import measure, morphology
from skimage.filters import threshold_multiotsu
from skimage.measure import label, regionprops
from skimage.morphology import (
    dilation,
    remove_small_objects,
    skeletonize,
    square,
)
from skimage.segmentation import find_boundaries, relabel_sequential, watershed
from skimage.util import invert as invertimage
from tifffile import imread, imwrite
from tqdm import tqdm
from .CellPose3D import CellPose3DModel
from .PredictTiledLoader import PredictTiled
from vollseg.matching import matching
from vollseg.nmslabel import NMSLabel
from vollseg.seedpool import SeedPool
from vollseg.unetstarmask import UnetStarMask
from .Tiles_3D import VolumeSlicer

Boxname = "ImageIDBox"
GLOBAL_THRESH = 1.0e-2
GLOBAL_ERODE = 8


class SegCorrect:
    def __init__(self, imagedir, segmentationdir):

        self.imagedir = imagedir
        self.segmentationdir = segmentationdir

    def showNapari(self):

        self.viewer = napari.Viewer()
        Raw_path = os.path.join(self.imagedir, "*tif")
        X = glob.glob(Raw_path)
        Imageids = []
        Seg_path = os.path.join(self.segmentationdir, "*tif")
        Y = glob.glob(Seg_path)
        SegImageids = []
        for imagename in X:
            Imageids.append(imagename)
        for imagename in Y:
            SegImageids.append(imagename)

        imageidbox = QComboBox()
        imageidbox.addItem(Boxname)
        savebutton = QPushButton(" Save Corrections")

        for i in range(0, len(Imageids)):

            imageidbox.addItem(str(Imageids[i]))

        imageidbox.currentIndexChanged.connect(
            lambda trackid=imageidbox: self.image_add(
                imageidbox.currentText(),
                self.segmentationdir
                + "/"
                + os.path.basename(
                    os.path.splitext(imageidbox.currentText())[0]
                )
                + ".tif",
                os.path.basename(
                    os.path.splitext(imageidbox.currentText())[0]
                ),
                False,
            )
        )

        savebutton.clicked.connect(
            lambda trackid=imageidbox: self.image_add(
                imageidbox.currentText(),
                self.segmentationdir
                + "/"
                + os.path.basename(
                    os.path.splitext(imageidbox.currentText())[0]
                )
                + ".tif",
                os.path.basename(
                    os.path.splitext(imageidbox.currentText())[0]
                ),
                True,
            )
        )

        self.viewer.window.add_dock_widget(
            imageidbox, name="Image", area="bottom"
        )
        self.viewer.window.add_dock_widget(
            savebutton, name="Save Segmentations", area="bottom"
        )

    def image_add(self, image_toread, seg_image_toread, imagename, save=False):

        if not save:
            for layer in list(self.viewer.layers):

                if "Image" in layer.name or layer.name in "Image":

                    self.viewer.layers.remove(layer)

            self.image = imread(image_toread)
            self.segimage = imread(seg_image_toread)

            self.viewer.add_image(self.image, name="Image" + imagename)
            self.viewer.add_labels(
                self.segimage, name="Image" + "Integer_Labels" + imagename
            )

        if save:

            ModifiedArraySeg = self.viewer.layers[
                "Image" + "Integer_Labels" + imagename
            ].data
            ModifiedArraySeg = ModifiedArraySeg.astype("uint16")
            imwrite(
                (self.segmentationdir + imagename + ".tif"), ModifiedArraySeg
            )


def BinaryLabel(BinaryImageOriginal, max_size=15000):

    BinaryImageOriginal = BinaryImageOriginal.astype("uint16")
    image = normalizeFloatZeroOne(BinaryImageOriginal)
    image = invertimage(image)
    IntegerImage = watershed(-image)
    AugmentedLabel = remove_big_objects(IntegerImage, max_size=max_size)

    return AugmentedLabel


def expand_labels(label_image, distance=1):
    """Expand labels in label image by ``distance`` pixels without overlapping.
    Given a label image, ``expand_labels`` grows label regions (connected components)
    outwards by up to ``distance`` pixels without overflowing into neighboring regions.
    More specifically, each background pixel that is within Euclidean distance
    of <= ``distance`` pixels of a connected component is assigned the label of that
    connected component.
    Where multiple connected components are within ``distance`` pixels of a background
    pixel, the label value of the closest connected component will be assigned (see
    Notes for the case of multiple labels at equal distance).
    Parameters
    ----------
    label_image : ndarray of dtype int
        label image
    distance : float
        Euclidean distance in pixels by which to grow the labels. Default is one.
    Returns
    -------
    enlarged_labels : ndarray of dtype int
        Labeled array, where all connected regions have been enlarged
    Notes
    -----
    Where labels are spaced more than ``distance`` pixels are apart, this is
    equivalent to a morphological dilation with a disc or hyperball of radius ``distance``.
    However, in contrast to a morphological dilation, ``expand_labels`` will
    not expand a label region into a neighboring region.
    This implementation of ``expand_labels`` is derived from CellProfiler [1]_, where
    it is known as module "IdentifySecondaryObjects (Distance-N)" [2]_.
    There is an important edge case when a pixel has the same distance to
    multiple regions, as it is not defined which region expands into that
    space. Here, the exact behavior depends on the upstream implementation
    of ``scipy.model_dimage.distance_transform_edt``.
    See Also
    --------
    :func:`skimage.measure.label`, :func:`skimage.segmentation.watershed`, :func:`skimage.morphology.dilation`
    References
    ----------
    .. [1] https://cellprofiler.org
    .. [2] https://github.com/CellProfiler/CellProfiler/blob/082930ea95add7b72243a4fa3d39ae5145995e9c/cellprofiler/modules/identifysecondaryobjects.py#L559
    Examples
    --------
    >>> labels = np.array([0, 1, 0, 0, 0, 0, 2])
    >>> expand_labels(labels, distance=1)
    array([1, 1, 1, 0, 0, 2, 2])
    Labels will not overwrite each other:
    >>> expand_labels(labels, distance=3)
    array([1, 1, 1, 1, 2, 2, 2])
    In case of ties, behavior is undefined, but currently resolves to the
    label closest to ``(0,) * model_dim`` in lexicographical order.
    >>> labels_tied = np.array([0, 1, 0, 2, 0])
    >>> expand_labels(labels_tied, 1)
    array([1, 1, 1, 2, 2])
    >>> labels2d = np.array(
    ...     [[0, 1, 0, 0],
    ...      [2, 0, 0, 0],
    ...      [0, 3, 0, 0]]
    ... )
    >>> expand_labels(labels2d, 1)
    array([[2, 1, 1, 0],
           [2, 2, 0, 0],
           [2, 3, 3, 0]])
    """

    distances, nearest_label_coords = distance_transform_edt(
        label_image == 0, return_indices=True
    )
    labels_out = np.zeros_like(label_image)
    dilate_mask = distances <= distance
    # build the coordinates to find nearest labels,
    # in contrast to [1] this implementation supports label arrays
    # of any dimension
    masked_nearest_label_coords = [
        dimension_indices[dilate_mask]
        for dimension_indices in nearest_label_coords
    ]
    nearest_labels = label_image[tuple(masked_nearest_label_coords)]
    labels_out[dilate_mask] = nearest_labels
    return labels_out


def SimplePrediction(
    x,
    UnetModel,
    StarModel,
    n_tiles=(2, 2),
    UseProbability=True,
    min_size=20,
    axes="ZYX",
    ExpandLabels=True,
):

    Mask = UNETPrediction3D(x, UnetModel, n_tiles, axes, ExpandLabels)

    smart_seeds, _, _, _ = STARPrediction3D(
        x,
        axes,
        StarModel,
        n_tiles,
        unet_mask=Mask,
        smartcorrection=None,
        UseProbability=UseProbability,
    )

    smart_seeds = smart_seeds.astype("uint16")

    return smart_seeds


def fill_label_holes(lbl_img, **kwargs):
    """Fill small holes in label image."""
    # TODO: refactor 'fill_label_holes' and 'edt_prob' to share code
    def grow(sl, interior):
        return tuple(
            slice(s.start - int(w[0]), s.stop + int(w[1]))
            for s, w in zip(sl, interior)
        )

    def shrink(interior):
        return tuple(
            slice(int(w[0]), (-1 if w[1] else None)) for w in interior
        )

    objects = find_objects(lbl_img)
    lbl_img_filled = np.zeros_like(lbl_img)
    for i, sl in enumerate(objects, 1):
        if sl is None:
            continue
        interior = [
            (s.start > 0, s.stop < sz) for s, sz in zip(sl, lbl_img.shape)
        ]
        shrink_slice = shrink(interior)
        grown_mask = lbl_img[grow(sl, interior)] == i
        mask_filled = binary_fill_holes(grown_mask, **kwargs)[shrink_slice]
        lbl_img_filled[sl][mask_filled] = i
    return lbl_img_filled


def dilate_label_holes(lbl_img, iterations):
    lbl_img_filled = np.zeros_like(lbl_img)
    for lb in range(np.min(lbl_img), np.max(lbl_img) + 1):
        mask = lbl_img == lb
        mask_filled = binary_dilation(mask, iterations=iterations)
        lbl_img_filled[mask_filled] = lb
    return lbl_img_filled


def match_labels(ys, iou_threshold=0.5):
    """
    Matches object ids in a list of label images based on a matching criterion.
    For i=0..len(ys)-1 consecutively matches ys[i+1] with ys[i],
    matching objects retain their id, non matched objects will be assigned a new id
    Example
    -------
    import numpy as np
    from stardist.data import test_image_nuclei_2d
    from stardist.matching import match_labels
    _y = test_image_nuclei_2d(return_mask=True)[1]
    labels = np.stack([_y, 2*np.roll(_y,10)], axis=0)
    labels_new = match_labels(labels)
    Parameters
    ----------
    ys : np.ndarray, tuple of np.ndarray
          list/array of integer labels (2D or 3D)
    """
    ys = np.asarray(ys)
    if ys.model_dim not in (3, 4):
        raise ValueError("label image y should be 3 or 4 dimensional!")

    def _match_single(x, y):
        res = matching(x, y, report_matches=True)

        pairs = tuple(
            p
            for p, s in zip(res.matched_pairs, res.matched_scores)
            if s >= iou_threshold
        )
        map_dict = {i2: i1 for i1, i2 in pairs}

        y2 = np.zeros_like(y)
        y_labels = set(np.unique(y)) - {0}

        # labels that can be used for non-matched objects
        label_reservoir = list(
            set(np.arange(1, len(y_labels) + 1)) - set(map_dict.values())
        )
        for r in regionprops(y):
            m = y[r.slice] == r.label
            if r.label in map_dict:
                y2[r.slice][m] = map_dict[r.label]
            else:
                y2[r.slice][m] = label_reservoir.pop(0)

        return y2

    ys_new = ys.copy()

    for i in tqdm(range(len(ys) - 1)):
        ys_new[i + 1] = _match_single(ys_new[i], ys[i + 1])

    return ys_new


def remove_big_objects(ar: np.ndarray, max_size):

    out = ar.copy()
    ccs = out

    try:
        component_sizes = np.bincount(ccs.ravel())
    except ValueError:
        raise ValueError(
            "Negative value labels are not supported. Try "
            "relabeling the input with `scipy.model_dimage.label` or "
            "`skimage.morphology.label`."
        )

    too_big = component_sizes > max_size
    too_big_mask = too_big[ccs]
    out[too_big_mask] = 0

    return out


def BinaryDilation(Image, iterations=1):

    DilatedImage = binary_dilation(Image, iterations=iterations)

    return DilatedImage


def CCLabels(fname, max_size=15000):

    BinaryImageOriginal = imread(fname)
    Orig = normalizeFloatZeroOne(BinaryImageOriginal)
    InvertedBinaryImage = invertimage(BinaryImageOriginal)
    BinaryImage = normalizeFloatZeroOne(InvertedBinaryImage)
    image = binary_dilation(BinaryImage)
    image = invertimage(image)
    IntegerImage = label(image)
    labelclean = remove_big_objects(IntegerImage, max_size=max_size)
    AugmentedLabel = dilation(labelclean, footprint=square(3))
    AugmentedLabel = np.multiply(AugmentedLabel, Orig)

    return AugmentedLabel


def CreateTrackMate_CSV(Label, Name, savedir):

    TimeList = []

    XList = []
    YList = []
    TrackIDList = []
    QualityList = []
    print("Image has shape:", Label.shape)
    print("Image Dimensions:", len(Label.shape))

    CurrentSegimage = Label.astype("uint16")
    properties = measure.regionprops(CurrentSegimage)
    for prop in properties:

        T = prop.centroid[0]
        Y = prop.centroid[1]
        X = prop.centroid[2]
        regionlabel = prop.label
        sizeZ = abs(prop.bbox[0] - prop.bbox[3])
        sizeY = abs(prop.bbox[1] - prop.bbox[4])
        sizeX = abs(prop.bbox[2] - prop.bbox[5])
        volume = sizeZ * sizeX * sizeY
        radius = math.pow(3 * volume / (4 * math.pi), 1.0 / 3.0)
        TimeList.append(int(T))
        XList.append(int(X))
        YList.append(int(Y))
        TrackIDList.append(regionlabel)
        QualityList.append(radius)

    df = pd.DataFrame(
        list(zip(XList, YList, TimeList, TrackIDList, QualityList)),
        index=None,
        columns=["POSITION_X", "POSITION_Y", "FRAME", "TRACK_ID", "QUALITY"],
    )

    df.to_csv(savedir + "/" + "TrackMate_csv" + Name + ".csv", index=False)


def SmartSkel(smart_seedsLabels, ProbImage, RGB=False):

    if RGB:
        return smart_seedsLabels > 0
    SegimageB = find_boundaries(smart_seedsLabels)
    invertProbimage = 1 - ProbImage
    image_max = np.add(invertProbimage, SegimageB)

    pixel_condition = image_max < 1.2
    pixel_replace_condition = 0
    image_max = image_conditionals(
        image_max, pixel_condition, pixel_replace_condition
    )

    skeleton = skeletonize(image_max.astype("uint16") > 0)

    return skeleton


def Skel(smart_seedsLabels, RGB=False):

    if RGB:
        return smart_seedsLabels > 0
    image_max = find_boundaries(smart_seedsLabels)

    skeleton = skeletonize(image_max.astype("uint16") > 0)

    return skeleton


# If there are neighbouring seeds we do not put more seeds


def Region_embedding(image, region, sourceimage, RGB=False):

    returnimage = np.zeros(image.shape)
    if len(region) == 4 and len(image.shape) == 2:
        rowstart = region[0]
        colstart = region[1]
        endrow = region[2]
        endcol = region[3]
        returnimage[rowstart:endrow, colstart:endcol] = sourceimage
    if len(image.shape) == 3 and len(region) == 6 and RGB is False:
        zstart = region[0]
        rowstart = region[1]
        colstart = region[2]
        zend = region[3]
        endrow = region[4]
        endcol = region[5]
        returnimage[
            zstart:zend, rowstart:endrow, colstart:endcol
        ] = sourceimage

    if len(image.shape) == 3 and len(region) == 4 and RGB is False:
        rowstart = region[0]
        colstart = region[1]
        endrow = region[2]
        endcol = region[3]
        returnimage[
            0 : image.shape[0], rowstart:endrow, colstart:endcol
        ] = sourceimage

    if len(image.shape) == 3 and len(region) == 4 and RGB:
        returnimage = returnimage[:, :, 0]
        rowstart = region[0]
        colstart = region[1]
        endrow = region[2]
        endcol = region[3]
        returnimage[rowstart:endrow, colstart:endcol] = sourceimage

    return returnimage


def VollSeg2D(
    image,
    unet_model,
    star_model,
    noise_model=None,
    roi_model=None,
    prob_thresh=None,
    nms_thresh=None,
    axes="YX",
    min_size_mask=5,
    min_size=5,
    max_size=10000000,
    dounet=True,
    n_tiles=(2, 2),
    ExpandLabels=True,
    donormalize=True,
    lower_perc=1,
    upper_perc=99.8,
    UseProbability=True,
    RGB=False,
    seedpool=True,
):

    print("Generating SmartSeed results")

    if star_model is not None:
        nms_thresh = star_model.thresholds[1]
    elif nms_thresh is not None:
        nms_thresh = nms_thresh
    else:
        nms_thresh = 0

    if RGB:
        axes = "YXC"
    if "T" in axes:
        axes = "YX"
        if RGB:
            axes = "YXC"
    if noise_model is not None:
        print("Denoising Image")

        image = noise_model.predict(
            image.astype("float32"), axes=axes, n_tiles=n_tiles
        )
        pixel_condition = image < 0
        pixel_replace_condition = 0
        image = image_conditionals(
            image, pixel_condition, pixel_replace_condition
        )

    Mask = None
    Mask_patch = None
    roi_image = None
    if roi_model is not None:
        model_dim = roi_model.config.n_dim
        assert model_dim == len(
            image.shape
        ), f"For 2D images the region of interest model has to be 2D, model provided had {model_dim} instead"
        Segmented = roi_model.predict(
            image.astype("float32"), "YX", n_tiles=n_tiles
        )
        try:
            thresholds = threshold_multiotsu(Segmented, classes=2)

            # Using the threshold values, we generate the three regions.
            regions = np.digitize(Segmented, bins=thresholds)
        except ValueError:

            regions = Segmented

        roi_image = regions > 0
        roi_image = label(roi_image)
        roi_bbox = Bbox_region(roi_image)
        if roi_bbox is not None:
            rowstart = roi_bbox[0]
            colstart = roi_bbox[1]
            endrow = roi_bbox[2]
            endcol = roi_bbox[3]
            region = (slice(rowstart, endrow), slice(colstart, endcol))
            # The actual pixels in that region.
            patch = image[region]
        else:

            patch = image
            region = (slice(0, image.shape[0]), slice(0, image.shape[1]))
            rowstart = 0
            colstart = 0
            endrow = image.shape[1]
            endcol = image.shape[0]
            roi_bbox = [colstart, rowstart, endcol, endrow]

    else:

        patch = image

        region = (slice(0, image.shape[0]), slice(0, image.shape[1]))
        rowstart = 0
        colstart = 0
        endrow = image.shape[1]
        endcol = image.shape[0]
        roi_bbox = [colstart, rowstart, endcol, endrow]
    if dounet:

        if unet_model is not None:
            print("UNET segmentation on Image")

            Segmented = unet_model.predict(
                image.astype("float32"), axes, n_tiles=n_tiles
            )
        else:
            Segmented = image
        if RGB:
            Segmented = Segmented[:, :, 0]

        try:
            thresholds = threshold_multiotsu(Segmented, classes=2)

            # Using the threshold values, we generate the three regions.
            regions = np.digitize(Segmented, bins=thresholds)
        except ValueError:

            regions = Segmented
        Binary = regions > 0
        Mask = Binary.copy()

        Mask = Region_embedding(image, roi_bbox, Mask, RGB=RGB)
        Mask_patch = Mask.copy()
    elif noise_model is not None and dounet is False:

        Mask = np.zeros(patch.shape)
        try:
            thresholds = threshold_multiotsu(patch, classes=2)

            # Using the threshold values, we generate the three regions.
            regions = np.digitize(patch, bins=thresholds)
        except ValueError:

            regions = patch
        Mask = regions > 0

        Mask = label(Mask)
        Mask = remove_small_objects(
            Mask.astype("uint16"), min_size=min_size_mask
        )
        Mask = remove_big_objects(Mask.astype("uint16"), max_size=max_size)

        if RGB:
            Mask = Mask[:, :, 0]
            Mask_patch = Mask_patch[:, :, 0]
        Mask = Region_embedding(image, roi_bbox, Mask, RGB=RGB)
        Mask_patch = Mask.copy()
    # Smart Seed prediction
    print("Stardist segmentation on Image")
    if RGB:
        axis = (0, 1, 2)
    else:
        axis = (0, 1)
    if donormalize:
        patch_star = normalize(
            patch.astype("float32"), lower_perc, upper_perc, axis=axis
        )
    else:
        patch_star = patch
    smart_seeds, markers, star_labels, probability_map = SuperSTARPrediction(
        patch_star,
        star_model,
        n_tiles,
        unet_mask=Mask_patch,
        UseProbability=UseProbability,
        prob_thresh=prob_thresh,
        nms_thresh=nms_thresh,
        seedpool=seedpool,
    )
    smart_seeds = remove_small_objects(
        smart_seeds.astype("uint16"), min_size=min_size
    )
    smart_seeds = remove_big_objects(
        smart_seeds.astype("uint16"), max_size=max_size
    )
    skeleton = SmartSkel(smart_seeds, probability_map, RGB)
    skeleton = skeleton > 0
    # For avoiding pixel level error
    if Mask is not None:
        Mask = expand_labels(Mask, distance=1)

    smart_seeds = expand_labels(smart_seeds, distance=1)

    smart_seeds = Region_embedding(image, roi_bbox, smart_seeds, RGB=RGB)
    markers = Region_embedding(image, roi_bbox, markers, RGB=RGB)
    star_labels = Region_embedding(image, roi_bbox, star_labels, RGB=RGB)
    probability_map = Region_embedding(
        image, roi_bbox, probability_map, RGB=RGB
    )
    skeleton = Region_embedding(image, roi_bbox, skeleton, RGB=RGB)
    if Mask is None:
        Mask = smart_seeds > 0

    if noise_model is None and roi_image is not None:
        return (
            smart_seeds.astype("uint16"),
            Mask.astype("uint16"),
            star_labels.astype("uint16"),
            probability_map,
            markers.astype("uint16"),
            skeleton.astype("uint16"),
            roi_image.astype("uint16"),
        )

    if noise_model is None and roi_image is None:
        return (
            smart_seeds.astype("uint16"),
            Mask.astype("uint16"),
            star_labels.astype("uint16"),
            probability_map,
            markers.astype("uint16"),
            skeleton.astype("uint16"),
        )

    if noise_model is not None and roi_image is not None:
        return (
            smart_seeds.astype("uint16"),
            Mask.astype("uint16"),
            star_labels.astype("uint16"),
            probability_map,
            markers.astype("uint16"),
            skeleton.astype("uint16"),
            image,
            roi_image.astype("uint16"),
        )

    if noise_model is not None and roi_image is None:
        return (
            smart_seeds.astype("uint16"),
            Mask.astype("uint16"),
            star_labels.astype("uint16"),
            probability_map,
            markers.astype("uint16"),
            skeleton.astype("uint16"),
            image,
        )


def VollSeg_nolabel_precondition(image, Finalimage):

    model_dim = len(image.shape)
    if model_dim == 3:
        for i in range(image.shape[0]):
            Finalimage[i] = expand_labels(Finalimage[i], distance=GLOBAL_ERODE)

    return Finalimage


def VollSeg_label_precondition(image, overall_mask, Finalimage):

    model_dim = len(image.shape)
    if model_dim == 3:
        for i in range(image.shape[0]):
            Finalimage[i] = expand_labels(Finalimage[i], distance=50)
        pixel_condition = overall_mask == 0
        pixel_replace_condition = 0
        Finalimage = image_conditionals(
            Finalimage, pixel_condition, pixel_replace_condition
        )

    return Finalimage


def VollSeg_label_expansion(image, overall_mask, Finalimage, skeleton, RGB):

    for i in range(image.shape[0]):
        Finalimage[i, :] = expand_labels(Finalimage[i, :], distance=50)
        skeleton[i, :] = Skel(Finalimage[i, :], RGB)
        skeleton[i, :] = skeleton[i, :] > 0
    pixel_condition = overall_mask == 0
    pixel_replace_condition = 0
    Finalimage = image_conditionals(
        Finalimage, pixel_condition, pixel_replace_condition
    )
    skeleton = image_conditionals(
        skeleton, pixel_condition, pixel_replace_condition
    )

    return Finalimage, skeleton


def VollSeg_nolabel_expansion(image, Finalimage, skeleton, RGB):

    for i in range(image.shape[0]):
        Finalimage[i, :] = expand_labels(
            Finalimage[i, :], distance=GLOBAL_ERODE
        )
        skeleton[i, :] = Skel(Finalimage[i, :], RGB)
        skeleton[i, :] = skeleton[i, :] > 0

    return Finalimage, skeleton


def VollSeg_unet(
    image,
    unet_model=None,
    roi_model=None,
    n_tiles=(2, 2),
    axes="YX",
    ExpandLabels=True,
    noise_model=None,
    min_size_mask=100,
    max_size=10000000,
    RGB=False,
    iou_threshold=0.3,
    slice_merge=False,
    dounet=True,
    erosion_iterations=15,
):

    model_dim = len(image.shape)
    if len(n_tiles) != model_dim:
        if model_dim == 3:
            n_tiles = (n_tiles[-3], n_tiles[-2], n_tiles[-1])
        if model_dim == 2:
            n_tiles = (n_tiles[-2], n_tiles[-1])

    if roi_model is None:
        if RGB:
            if n_tiles is not None:
                n_tiles = (n_tiles[0], n_tiles[1], 1)

        if noise_model is not None:
            image = noise_model.predict(
                image.astype("float32"), axes, n_tiles=n_tiles
            )
            pixel_condition = image < 0
            pixel_replace_condition = 0
            image = image_conditionals(
                image, pixel_condition, pixel_replace_condition
            )

        if dounet and unet_model is not None:
            Segmented = unet_model.predict(
                image.astype("float32"), axes, n_tiles=n_tiles
            )
        else:
            Segmented = image
        if RGB:
            Segmented = Segmented[:, :, 0]

        try:
            thresholds = threshold_multiotsu(Segmented, classes=2)

            # Using the threshold values, we generate the three regions.
            regions = np.digitize(Segmented, bins=thresholds)
        except ValueError:

            regions = Segmented
        Binary = regions > 0
        overall_mask = Binary.copy()

        if model_dim == 3:
            for i in range(image.shape[0]):
                overall_mask[i] = binary_dilation(
                    overall_mask[i], iterations=erosion_iterations
                )
                overall_mask[i] = binary_erosion(
                    overall_mask[i], iterations=erosion_iterations
                )
                overall_mask[i] = fill_label_holes(overall_mask[i])

        Binary = label(Binary)

        if model_dim == 2:
            Binary = remove_small_objects(
                Binary.astype("uint16"), min_size=min_size_mask
            )
            Binary = remove_big_objects(
                Binary.astype("uint16"), max_size=max_size
            )
            Binary = fill_label_holes(Binary)
            Finalimage = relabel_sequential(Binary)[0]
            skeleton = Skel(Finalimage, RGB)
            skeleton = skeleton > 0
        if model_dim == 3 and slice_merge:
            for i in range(image.shape[0]):
                Binary[i] = label(Binary[i])

            Binary = match_labels(Binary, iou_threshold=iou_threshold)
            Binary = fill_label_holes(Binary)

        if model_dim == 3:
            for i in range(image.shape[0]):
                Binary[i] = remove_small_objects(
                    Binary[i].astype("uint16"), min_size=min_size_mask
                )
                Binary[i] = remove_big_objects(
                    Binary[i].astype("uint16"), max_size=max_size
                )
            Finalimage = relabel_sequential(Binary)[0]
            skeleton = Skel(Finalimage)

            if ExpandLabels:

                Finalimage, skeleton = VollSeg_label_expansion(
                    image, overall_mask, Finalimage, skeleton, RGB
                )

    elif roi_model is not None:

        if noise_model is not None:
            image = noise_model.predict(
                image.astype("float32"), axes, n_tiles=n_tiles
            )

            pixel_condition = image < 0
            pixel_replace_condition = 0
            image = image_conditionals(
                image, pixel_condition, pixel_replace_condition
            )

        model_dim = roi_model.config.n_dim
        if model_dim < len(image.shape):
            if len(n_tiles) == len(image.shape):
                tiles = (n_tiles[1], n_tiles[2])
            else:
                tiles = n_tiles
            maximage = np.amax(image, axis=0)
            Segmented = roi_model.predict(
                maximage.astype("float32"), "YX", n_tiles=tiles
            )
            try:
                thresholds = threshold_multiotsu(Segmented, classes=2)

                # Using the threshold values, we generate the three regions.
                regions = np.digitize(Segmented, bins=thresholds)
            except ValueError:

                regions = Segmented

            s_Binary = regions > 0

            s_Binary = label(s_Binary)
            s_Binary = remove_small_objects(
                s_Binary.astype("uint16"), min_size=min_size_mask
            )
            s_Binary = remove_big_objects(
                s_Binary.astype("uint16"), max_size=max_size
            )
            s_Binary = fill_label_holes(s_Binary)

            s_Finalimage = relabel_sequential(s_Binary)[0]

            s_skeleton = Skel(s_Finalimage)
            Binary = np.zeros_like(image)
            skeleton = np.zeros_like(image)
            Finalimage = np.zeros_like(image)
            for i in range(0, image.shape[0]):

                Binary[i] = s_Binary
                skeleton[i] = s_skeleton
                Finalimage[i] = s_Finalimage

        elif model_dim == len(image.shape):

            Segmented = roi_model.predict(
                image.astype("float32"), "YX", n_tiles=n_tiles
            )
            try:
                thresholds = threshold_multiotsu(Segmented, classes=2)

                # Using the threshold values, we generate the three regions.
                regions = np.digitize(Segmented, bins=thresholds)
            except ValueError:

                regions = Segmented

            Binary = regions > 0

            Binary = label(Binary)
            if model_dim == 3 and slice_merge:
                for i in range(image.shape[0]):
                    Binary[i] = label(Binary[i])

                Binary = match_labels(Binary, iou_threshold=iou_threshold)
                Binary = fill_label_holes(Binary)
                for i in range(image.shape[0]):
                    Binary[i] = remove_small_objects(
                        Binary[i].astype("uint16"), min_size=min_size_mask
                    )
                    Binary[i] = remove_big_objects(
                        Binary[i].astype("uint16"), max_size=max_size
                    )

            Finalimage = relabel_sequential(Binary)[0]

            skeleton = Skel(Finalimage)

    return Finalimage.astype("uint16"), skeleton, image


def _cellpose_3D_time_block(
    cellpose_model_3D_pretrained_file,
    image_membrane,
    patch_size,
    in_channels,
    out_activation,
    network_type,
    norm_method,
    background_weight,
    flow_weight,
    out_channels,
    feat_channels,
    num_levels,
    overlap,
    crop,
):
    if cellpose_model_3D_pretrained_file is not None:

        cellres = tuple(
            zip(
                *tuple(
                    _apply_cellpose_network_3D(
                        cellpose_model_3D_pretrained_file,
                        _x,
                        patch_size=patch_size,
                        in_channels=in_channels,
                        out_activation=out_activation,
                        network_type=network_type,
                        norm_method=norm_method,
                        background_weight=background_weight,
                        flow_weight=flow_weight,
                        out_channels=out_channels,
                        feat_channels=feat_channels,
                        num_levels=num_levels,
                        overlap=overlap,
                        crop=crop,
                    )
                    for _x in tqdm(image_membrane)
                )
            )
        )

        return cellres


def _cellpose_time_block(
    cellpose_model,
    custom_cellpose_model,
    cellpose_model_name,
    image_membrane,
    diameter_cellpose,
    flow_threshold,
    cellprob_threshold,
    stitch_threshold,
    anisotropy,
    pretrained_cellpose_model_path,
    gpu,
    do_3D,
):

    if cellpose_model is not None:

        if custom_cellpose_model:
            cellpose_model = models.Cellpose(
                gpu=gpu, model_type=cellpose_model_name
            )
            if anisotropy is not None:
                cellres = tuple(
                    zip(
                        *tuple(
                            cellpose_model.eval(
                                _x,
                                diameter=diameter_cellpose,
                                flow_threshold=flow_threshold,
                                cellprob_threshold=cellprob_threshold,
                                stitch_threshold=stitch_threshold,
                                anisotropy=anisotropy,
                                tile=True,
                                do_3D=do_3D,
                            )
                            for _x in tqdm(image_membrane)
                        )
                    )
                )
            else:

                cellres = tuple(
                    zip(
                        *tuple(
                            cellpose_model.eval(
                                _x,
                                diameter=diameter_cellpose,
                                flow_threshold=flow_threshold,
                                cellprob_threshold=cellprob_threshold,
                                stitch_threshold=stitch_threshold,
                                tile=True,
                                do_3D=do_3D,
                            )
                            for _x in tqdm(image_membrane)
                        )
                    )
                )

        else:
            cellpose_model = models.CellposeModel(
                gpu=gpu, pretrained_model=pretrained_cellpose_model_path
            )
            if anisotropy is not None:
                cellres = tuple(
                    zip(
                        *tuple(
                            cellpose_model.eval(
                                _x,
                                diameter=diameter_cellpose,
                                flow_threshold=flow_threshold,
                                cellprob_threshold=cellprob_threshold,
                                stitch_threshold=stitch_threshold,
                                anisotropy=anisotropy,
                                tile=True,
                                do_3D=do_3D,
                            )
                            for _x in tqdm(image_membrane)
                        )
                    )
                )
            else:
                cellres = tuple(
                    zip(
                        *tuple(
                            cellpose_model.eval(
                                _x,
                                diameter=diameter_cellpose,
                                flow_threshold=flow_threshold,
                                cellprob_threshold=cellprob_threshold,
                                stitch_threshold=stitch_threshold,
                                tile=True,
                                do_3D=do_3D,
                            )
                            for _x in tqdm(image_membrane)
                        )
                    )
                )

    return cellres


def _star_time_block(
    image_nuclei,
    unet_model,
    star_model,
    roi_model,
    ExpandLabels,
    axes,
    noise_model,
    prob_thresh,
    nms_thresh,
    donormalize,
    n_tiles,
    UseProbability,
    dounet,
    seedpool,
    slice_merge,
    iou_threshold,
    lower_perc,
    upper_perc,
    min_size_mask,
    min_size,
    max_size,
):

    if star_model is not None:
        if "T" in axes:
            axes = axes.replace("T", "")
        if prob_thresh is None and nms_thresh is None:
            prob_thresh = star_model.thresholds.prob
            nms_thresh = star_model.thresholds.nms
        res = tuple(
            zip(
                *tuple(
                    VollSeg3D(
                        image_nuclei[i],
                        unet_model,
                        star_model,
                        axes=axes,
                        noise_model=noise_model,
                        roi_model=roi_model,
                        ExpandLabels=ExpandLabels,
                        prob_thresh=prob_thresh,
                        nms_thresh=nms_thresh,
                        donormalize=donormalize,
                        lower_perc=lower_perc,
                        upper_perc=upper_perc,
                        min_size_mask=min_size_mask,
                        min_size=min_size,
                        max_size=max_size,
                        n_tiles=n_tiles,
                        UseProbability=UseProbability,
                        dounet=dounet,
                        seedpool=seedpool,
                        slice_merge=slice_merge,
                        iou_threshold=iou_threshold,
                    )
                    for i in tqdm(range(image_nuclei.shape[0]))
                )
            )
        )

    return res


def _cellpose_3D_star_time_block(
    cellpose_model_3D_pretrained_file,
    image_membrane,
    image_nuclei,
    patch_size,
    in_channels,
    out_activation,
    network_type,
    norm_method,
    background_weight,
    flow_weight,
    out_channels,
    feat_channels,
    num_levels,
    overlap,
    crop,
    unet_model,
    star_model,
    roi_model,
    ExpandLabels,
    axes,
    noise_model,
    prob_thresh,
    nms_thresh,
    donormalize,
    n_tiles,
    UseProbability,
    dounet,
    seedpool,
    iou_threshold,
    lower_perc,
    upper_perc,
    min_size_mask,
    min_size,
    max_size,
):

    futures = []

    with concurrent.futures.ThreadPoolExecutor(
        max_workers=os.cpu_count()
    ) as executor:

        futures.append(
            executor.submit(
                _star_time_block,
                image_nuclei,
                unet_model,
                star_model,
                roi_model,
                ExpandLabels,
                axes,
                noise_model,
                prob_thresh,
                nms_thresh,
                donormalize,
                n_tiles,
                UseProbability,
                dounet,
                seedpool,
                iou_threshold,
                lower_perc,
                upper_perc,
                min_size_mask,
                min_size,
                max_size,
            )
        )

        futures.append(
            executor.submit(
                _cellpose_3D_time_block(
                    cellpose_model_3D_pretrained_file,
                    image_membrane,
                    patch_size,
                    in_channels,
                    out_activation,
                    network_type,
                    norm_method,
                    background_weight,
                    flow_weight,
                    out_channels,
                    feat_channels,
                    num_levels,
                    overlap,
                    crop,
                )
            )
        )
        results = [r.result() for r in futures]

        res, cellres = results

    return cellres, res


def _cellpose_star_time_block(
    cellpose_model,
    custom_cellpose_model,
    cellpose_model_name,
    image_membrane,
    image_nuclei,
    diameter_cellpose,
    flow_threshold,
    cellprob_threshold,
    stitch_threshold,
    anisotropy,
    pretrained_cellpose_model_path,
    gpu,
    unet_model,
    star_model,
    roi_model,
    ExpandLabels,
    axes,
    noise_model,
    prob_thresh,
    nms_thresh,
    donormalize,
    n_tiles,
    UseProbability,
    dounet,
    seedpool,
    slice_merge,
    iou_threshold,
    lower_perc,
    upper_perc,
    min_size_mask,
    min_size,
    max_size,
    do_3D,
):

    futures = []

    with concurrent.futures.ThreadPoolExecutor(
        max_workers=os.cpu_count()
    ) as executor:

        futures.append(
            executor.submit(
                _star_time_block,
                image_nuclei,
                unet_model,
                star_model,
                roi_model,
                ExpandLabels,
                axes,
                noise_model,
                prob_thresh,
                nms_thresh,
                donormalize,
                n_tiles,
                UseProbability,
                dounet,
                seedpool,
                slice_merge,
                iou_threshold,
                lower_perc,
                upper_perc,
                min_size_mask,
                min_size,
                max_size,
            )
        )

        futures.append(
            executor.submit(
                _cellpose_time_block,
                cellpose_model,
                custom_cellpose_model,
                cellpose_model_name,
                image_membrane,
                diameter_cellpose,
                flow_threshold,
                cellprob_threshold,
                stitch_threshold,
                anisotropy,
                pretrained_cellpose_model_path,
                gpu,
                do_3D,
            )
        )
        results = [r.result() for r in futures]

        res, cellres = results

    return cellres, res


def collate_fn(data):

    slices = []
    input_tensor = []
    for x, y in data:

        if len(input_tensor) == 0:
            input_tensor = torch.stack([x])
        else:
            input_tensor = torch.stack([input_tensor, x])

        slices.append(y)

    return input_tensor, slices


def _apply_cellpose_network_3D(
    cellpose_model_3D_pretrained_file,
    image_membrane,
    patch_size=(8, 256, 256),
    in_channels=1,
    out_activation="tanh",
    network_type="unet",
    norm_method="instance",
    background_weight=1,
    flow_weight=1,
    out_channels=4,
    feat_channels=16,
    num_levels=3,
    overlap=(1, 16, 16),
    crop=(2, 32, 32),
):

    hparams = {
        "patch_size": patch_size,
        "in_channels": in_channels,
        "out_channels": out_channels,
        "feat_channels": feat_channels,
        "num_levels": num_levels,
        "out_activation": out_activation,
        "norm_method": norm_method,
        "background_weight": background_weight,
        "flow_weight": flow_weight,
        "learning_rate": 0.01,
        "network_type": network_type,
    }

    start = cputime.time()
    torch.cuda.empty_cache()
    gc.collect()
    model = CellPose3DModel(hparams=hparams)
    model = model.load_from_checkpoint(cellpose_model_3D_pretrained_file)
    try:
        model = model.cuda()
    except ValueError:
        model = model.cpu()

    model.eval()
    predict_tiler = VolumeSlicer(image_membrane, patch_size, overlap, crop)

    dataset = PredictTiled(
        tiler=predict_tiler,
        image=image_membrane,
        patch_size=patch_size,
        overlap=overlap,
        crop=crop,
    )

    dataset.tiler.get_fading_map()
    fading_map = np.repeat(
        dataset.tiler.fading_map[np.newaxis, ...], out_channels, axis=0
    )

    working_size = tuple(
        np.max(np.array(dataset.tiler.locations), axis=0)
        - np.min(np.array(dataset.tiler.locations), axis=0)
        + np.array(patch_size)
    )

    # Initialize maps (random to overcome memory leaks)
    predicted_img = np.full(
        (out_channels,) + working_size, 0, dtype=np.float32
    )
    norm_map = np.full((out_channels,) + working_size, 0, dtype=np.float32)

    for patch_idx in range(dataset.__len__()):

        sample = dataset.__getitem__(patch_idx)
        data = torch.autograd.Variable(
            torch.from_numpy(sample[np.newaxis, ...]).cuda()
        )
        data = data.float()

        # Predict the image
        pred_patch = model(data)
        pred_patch = pred_patch.cpu().data.numpy()
        pred_patch = np.squeeze(pred_patch)

        # Get the current slice position
        slicing = tuple(
            map(
                slice,
                (0,)
                + tuple(
                    dataset.tiler.patch_start
                    + dataset.tiler.global_crop_before
                ),
                (out_channels,)
                + tuple(
                    dataset.tiler.patch_end + dataset.tiler.global_crop_before
                ),
            )
        )

        # Add predicted patch and fading weights to the corresponding maps
        predicted_img[slicing] = (
            predicted_img[slicing] + pred_patch * fading_map
        )
        norm_map[slicing] = norm_map[slicing] + fading_map

    predicted_img = predicted_img.detach().cpu().numpy()
    slicing = tuple(
        map(
            slice,
            (0,) + tuple(dataset.tiler.global_crop_before),
            (hparams.out_channels,) + tuple(dataset.tiler.global_crop_after),
        )
    )
    predicted_img = predicted_img[slicing]

    # Save the predicted image
    predicted_img = np.transpose(predicted_img, (1, 2, 3, 0))
    predicted_img = predicted_img.astype(np.float32)
    print(
        f"cellpose in 3D done, {predicted_img.shape}, took {cputime.time() - start} seconds"
    )

    foreground_map = predicted_img[0, ...]
    try:
        thresholds = threshold_multiotsu(foreground_map, classes=2)

        # Using the threshold values, we generate the three regions.
        regions = np.digitize(foreground_map, bins=thresholds)
        foreground_map = regions > 0
    except ValueError:

        foreground_map = foreground_map > 0
    predicted_img = predicted_img[1:, ...]
    projection_axis = 0
    flow_img = np.amax(predicted_img, axis=projection_axis)

    print("returning cellpose map", flow_img.shape)
    torch.cuda.empty_cache()
    gc.collect()

    return foreground_map, flow_img


def VollCellPose3D(
    image: np.ndarray,
    channel_membrane: int = 0,
    channel_nuclei: int = 1,
    cellpose_model_3D_pretrained_file=None,
    patch_size=(8, 256, 256),
    in_channels=1,
    out_activation="tanh",
    network_type="unet",
    norm_method="instance",
    background_weight=1,
    flow_weight=1,
    out_channels=4,
    feat_channels=16,
    num_levels=4,
    overlap=(1, 16, 16),
    crop=(2, 32, 32),
    star_model=None,
    unet_model=None,
    roi_model=None,
    noise_model=None,
    axes: str = "ZYX",
    prob_thresh: float = None,
    ExpandLabels: bool = False,
    nms_thresh: float = None,
    min_size_mask: int = 10,
    min_size: int = 10,
    max_size: int = 10000,
    n_tiles: tuple = (1, 1, 1),
    UseProbability: bool = True,
    donormalize: bool = True,
    lower_perc: float = 1.0,
    upper_perc: float = 99.8,
    dounet: bool = True,
    seedpool: bool = True,
    save_dir: str = None,
    Name: str = "Result",
    iou_threshold: float = 0.3,
    z_thresh: int = 2,
):

    if prob_thresh is None and nms_thresh is None:
        prob_thresh = star_model.thresholds.prob
        nms_thresh = star_model.thresholds.nms

    if len(image.shape) == 3 and "T" not in axes:
        # Just a 3D image
        image_membrane = image
        image_nuclei = image

        cellres, res = _cellpose_3D_star_block(
            cellpose_model_3D_pretrained_file,
            image_membrane,
            image_nuclei,
            unet_model,
            star_model,
            roi_model,
            patch_size,
            in_channels,
            out_activation,
            network_type,
            norm_method,
            background_weight,
            flow_weight,
            out_channels,
            feat_channels,
            num_levels,
            overlap,
            crop,
            ExpandLabels,
            axes,
            noise_model,
            prob_thresh,
            nms_thresh,
            donormalize,
            n_tiles,
            UseProbability,
            dounet,
            seedpool,
            iou_threshold,
            lower_perc,
            upper_perc,
            min_size_mask,
            min_size,
            max_size,
        )

    if len(image.shape) == 4 and "T" not in axes:
        image_membrane = image[:, channel_membrane, :, :]
        image_nuclei = image[:, channel_nuclei, :, :]

        cellres, res = _cellpose_3D_star_block(
            cellpose_model_3D_pretrained_file,
            image_membrane,
            image_nuclei,
            unet_model,
            star_model,
            roi_model,
            patch_size,
            in_channels,
            out_activation,
            network_type,
            norm_method,
            background_weight,
            flow_weight,
            out_channels,
            feat_channels,
            num_levels,
            overlap,
            crop,
            ExpandLabels,
            axes,
            noise_model,
            prob_thresh,
            nms_thresh,
            donormalize,
            n_tiles,
            UseProbability,
            dounet,
            seedpool,
            iou_threshold,
            lower_perc,
            upper_perc,
            min_size_mask,
            min_size,
            max_size,
        )

    if len(image.shape) > 4 and "T" in axes:

        if len(n_tiles) == 4:
            n_tiles = (n_tiles[1], n_tiles[2], n_tiles[3])
        image_membrane = image[:, :, channel_membrane, :, :]
        image_nuclei = image[:, :, channel_nuclei, :, :]
        cellres, res = _cellpose_3D_star_time_block(
            cellpose_model_3D_pretrained_file,
            image_membrane,
            image_nuclei,
            patch_size,
            in_channels,
            out_activation,
            network_type,
            norm_method,
            background_weight,
            flow_weight,
            out_channels,
            feat_channels,
            num_levels,
            overlap,
            crop,
            unet_model,
            star_model,
            roi_model,
            ExpandLabels,
            axes,
            noise_model,
            prob_thresh,
            nms_thresh,
            donormalize,
            n_tiles,
            UseProbability,
            dounet,
            seedpool,
            iou_threshold,
            lower_perc,
            upper_perc,
            min_size_mask,
            min_size,
            max_size,
        )

    foreground, flows = cellres

    if (
        noise_model is None
        and star_model is not None
        and roi_model is not None
        and cellpose_model_3D_pretrained_file is None
    ):
        (
            sized_smart_seeds,
            instance_labels,
            star_labels,
            probability_map,
            markers,
            skeleton,
            roi_image,
        ) = res

    if (
        noise_model is None
        and star_model is not None
        and roi_model is not None
        and cellpose_model_3D_pretrained_file is not None
    ):
        (
            sized_smart_seeds,
            instance_labels,
            star_labels,
            probability_map,
            markers,
            skeleton,
            roi_image,
        ) = res
        sized_smart_seeds = np.asarray(sized_smart_seeds)
        instance_labels = np.asarray(instance_labels)
        star_labels = np.asarray(star_labels)
        probability_map = np.asarray(probability_map)
        markers = np.asarray(markers)
        skeleton = np.asarray(skeleton)
        roi_image = np.asarray(roi_image)
        voll_cell_seg = _cellpose_3D_block(
            axes,
            sized_smart_seeds,
            foreground,
            flows,
            nms_thresh,
            z_thresh=z_thresh,
        )

    if (
        noise_model is None
        and star_model is not None
        and roi_model is None
        and cellpose_model_3D_pretrained_file is None
    ):
        (
            sized_smart_seeds,
            instance_labels,
            star_labels,
            probability_map,
            markers,
            skeleton,
        ) = res

    if (
        noise_model is None
        and star_model is not None
        and roi_model is None
        and cellpose_model_3D_pretrained_file is not None
    ):
        (
            sized_smart_seeds,
            instance_labels,
            star_labels,
            probability_map,
            markers,
            skeleton,
        ) = res
        sized_smart_seeds = np.asarray(sized_smart_seeds)
        instance_labels = np.asarray(instance_labels)
        star_labels = np.asarray(star_labels)
        probability_map = np.asarray(probability_map)
        markers = np.asarray(markers)
        skeleton = np.asarray(skeleton)
        voll_cell_seg = _cellpose_3D_block(
            axes,
            sized_smart_seeds,
            foreground,
            flows,
            nms_thresh,
            z_thresh=z_thresh,
        )

    if (
        noise_model is not None
        and star_model is not None
        and roi_model is not None
        and cellpose_model_3D_pretrained_file is None
    ):
        (
            sized_smart_seeds,
            instance_labels,
            star_labels,
            probability_map,
            markers,
            skeleton,
            image,
            roi_image,
        ) = res

    if (
        noise_model is not None
        and star_model is not None
        and roi_model is not None
        and cellpose_model_3D_pretrained_file is not None
    ):
        (
            sized_smart_seeds,
            instance_labels,
            star_labels,
            probability_map,
            markers,
            skeleton,
            image,
            roi_image,
        ) = res
        sized_smart_seeds = np.asarray(sized_smart_seeds)
        instance_labels = np.asarray(instance_labels)
        star_labels = np.asarray(star_labels)
        probability_map = np.asarray(probability_map)
        markers = np.asarray(markers)
        skeleton = np.asarray(skeleton)
        image = np.asarray(image)
        roi_image = np.asarray(roi_image)
        voll_cell_seg = _cellpose_3D_block(
            axes,
            sized_smart_seeds,
            foreground,
            flows,
            nms_thresh,
            z_thresh=z_thresh,
        )

    if (
        noise_model is not None
        and star_model is not None
        and roi_model is None
        and cellpose_model_3D_pretrained_file is not None
    ):
        (
            sized_smart_seeds,
            instance_labels,
            star_labels,
            probability_map,
            markers,
            skeleton,
            image,
        ) = res
        (
            sized_smart_seeds,
            instance_labels,
            star_labels,
            probability_map,
            markers,
            skeleton,
            roi_image,
        ) = res
        sized_smart_seeds = np.asarray(sized_smart_seeds)
        instance_labels = np.asarray(instance_labels)
        star_labels = np.asarray(star_labels)
        probability_map = np.asarray(probability_map)
        markers = np.asarray(markers)
        skeleton = np.asarray(skeleton)
        roi_image = np.asarray(roi_image)
        voll_cell_seg = _cellpose_3D_block(
            axes,
            sized_smart_seeds,
            foreground,
            flows,
            nms_thresh,
            z_thresh=z_thresh,
        )

    if (
        noise_model is None
        and star_model is not None
        and roi_model is not None
        and cellpose_model_3D_pretrained_file is not None
    ):

        (
            sized_smart_seeds,
            instance_labels,
            star_labels,
            probability_map,
            markers,
            skeleton,
            roi_image,
        ) = res
        sized_smart_seeds = np.asarray(sized_smart_seeds)
        instance_labels = np.asarray(instance_labels)
        star_labels = np.asarray(star_labels)
        probability_map = np.asarray(probability_map)
        markers = np.asarray(markers)
        skeleton = np.asarray(skeleton)
        roi_image = np.asarray(roi_image)
        voll_cell_seg = _cellpose_3D_block(
            axes,
            sized_smart_seeds,
            foreground,
            flows,
            nms_thresh,
            z_thresh=z_thresh,
        )

    elif (
        noise_model is not None
        and star_model is None
        and roi_model is None
        and unet_model is None
        and cellpose_model_3D_pretrained_file is not None
    ):

        instance_labels, skeleton, image = res

    elif (
        star_model is None
        and roi_model is None
        and unet_model is not None
        and noise_model is not None
        and cellpose_model_3D_pretrained_file is not None
    ):

        instance_labels, skeleton, image = res

    elif (
        star_model is None
        and roi_model is not None
        and unet_model is not None
        and noise_model is not None
        and cellpose_model_3D_pretrained_file is not None
    ):

        instance_labels, skeleton, image = res

    elif (
        star_model is None
        and roi_model is None
        and unet_model is not None
        and noise_model is None
        and cellpose_model_3D_pretrained_file is not None
    ):

        instance_labels, skeleton, image = res

    elif (
        star_model is None
        and roi_model is not None
        and unet_model is None
        and noise_model is None
        and cellpose_model_3D_pretrained_file is not None
    ):

        roi_image, skeleton, image = res
        instance_labels = roi_image

    elif (
        star_model is None
        and roi_model is not None
        and unet_model is None
        and noise_model is not None
        and cellpose_model_3D_pretrained_file is not None
    ):

        roi_image, skeleton, image = res
        instance_labels = roi_image

    elif (
        star_model is None
        and roi_model is not None
        and unet_model is not None
        and noise_model is None
        and cellpose_model_3D_pretrained_file is not None
    ):

        roi_image, skeleton, image = res
        instance_labels = roi_image

    if save_dir is not None:
        print("Saving Results ...")
        Path(save_dir).mkdir(exist_ok=True)

        if cellpose_model_3D_pretrained_file is not None:

            vollcellpose_results = os.path.join(save_dir, "VollCellPose3D/")
            vollcellpose_flows = os.path.join(save_dir, "CellPoseFlows/")
            vollcellpose_foreground = os.path.join(
                save_dir, "CellPoseForeground/"
            )
            flows = np.asarray(flows)
            Path(vollcellpose_results).mkdir(exist_ok=True)

            imwrite(
                (vollcellpose_results + Name + ".tif"),
                np.asarray(voll_cell_seg).astype("uint16"),
            )

            Path(vollcellpose_flows).mkdir(exist_ok=True)

            imwrite(
                (vollcellpose_flows + Name + ".tif"),
                np.asarray(flows).astype("float32"),
            )

            Path(vollcellpose_foreground).mkdir(exist_ok=True)

            imwrite(
                (vollcellpose_foreground + Name + ".tif"),
                np.asarray(foreground).astype("float32"),
            )

        if roi_model is not None:
            roi_results = os.path.join(save_dir, "Roi/")
            Path(roi_results).mkdir(exist_ok=True)
            imwrite(
                (roi_results + Name + ".tif"),
                np.asarray(roi_image).astype("uint16"),
            )

        if unet_model is not None:
            unet_results = os.path.join(save_dir, "BinaryMask/")
            skel_unet_results = os.path.join(save_dir, "skeleton/")
            Path(unet_results).mkdir(exist_ok=True)
            Path(skel_unet_results).mkdir(exist_ok=True)

            imwrite(
                (unet_results + Name + ".tif"),
                np.asarray(instance_labels).astype("uint16"),
            )
            imwrite(
                (skel_unet_results + Name + ".tif"),
                np.asarray(skeleton).astype("uint16"),
            )
        if star_model is not None:
            vollseg_results = os.path.join(save_dir, "VollSeg/")
            stardist_results = os.path.join(save_dir, "StarDist/")
            probability_results = os.path.join(save_dir, "Probability/")
            marker_results = os.path.join(save_dir, "markers/")
            skel_results = os.path.join(save_dir, "skeleton/")
            Path(skel_results).mkdir(exist_ok=True)
            Path(vollseg_results).mkdir(exist_ok=True)
            Path(stardist_results).mkdir(exist_ok=True)
            Path(probability_results).mkdir(exist_ok=True)
            Path(marker_results).mkdir(exist_ok=True)
            imwrite(
                (stardist_results + Name + ".tif"),
                np.asarray(star_labels).astype("uint16"),
            )
            imwrite(
                (vollseg_results + Name + ".tif"),
                np.asarray(sized_smart_seeds).astype("uint16"),
            )
            imwrite(
                (probability_results + Name + ".tif"),
                np.asarray(probability_map).astype("float32"),
            )
            imwrite(
                (marker_results + Name + ".tif"),
                np.asarray(markers).astype("uint16"),
            )
            imwrite((skel_results + Name + ".tif"), np.asarray(skeleton))
        if noise_model is not None:
            denoised_results = os.path.join(save_dir, "Denoised/")
            Path(denoised_results).mkdir(exist_ok=True)
            imwrite(
                (denoised_results + Name + ".tif"),
                np.asarray(image).astype("float32"),
            )

    # If denoising is not done but stardist and unet models are supplied we return the stardist, vollseg and semantic segmentation maps
    if (
        noise_model is None
        and star_model is not None
        and roi_model is not None
        and cellpose_model_3D_pretrained_file is None
    ):

        return (
            sized_smart_seeds,
            instance_labels,
            star_labels,
            probability_map,
            markers,
            skeleton,
            roi_image,
        )

    if (
        noise_model is None
        and star_model is not None
        and roi_model is not None
        and cellpose_model_3D_pretrained_file is not None
    ):

        return (
            sized_smart_seeds,
            instance_labels,
            star_labels,
            probability_map,
            markers,
            skeleton,
            roi_image,
            voll_cell_seg,
            flows,
        )

    elif (
        noise_model is None
        and star_model is not None
        and roi_model is None
        and cellpose_model_3D_pretrained_file is None
    ):

        return (
            sized_smart_seeds,
            instance_labels,
            star_labels,
            probability_map,
            markers,
            skeleton,
        )

    elif (
        noise_model is None
        and star_model is not None
        and roi_model is None
        and cellpose_model_3D_pretrained_file is not None
    ):

        return (
            sized_smart_seeds,
            instance_labels,
            star_labels,
            probability_map,
            markers,
            skeleton,
            voll_cell_seg,
            flows,
        )

    # If denoising is done and stardist and unet models are supplied we return the stardist, vollseg, denoised image and semantic segmentation maps
    elif (
        noise_model is not None
        and star_model is not None
        and roi_model is not None
        and cellpose_model_3D_pretrained_file is None
    ):

        return (
            sized_smart_seeds,
            instance_labels,
            star_labels,
            probability_map,
            markers,
            skeleton,
            image,
            roi_image,
        )

    elif (
        noise_model is not None
        and star_model is not None
        and roi_model is not None
        and cellpose_model_3D_pretrained_file is not None
    ):

        return (
            sized_smart_seeds,
            instance_labels,
            star_labels,
            probability_map,
            markers,
            skeleton,
            image,
            roi_image,
            voll_cell_seg,
            flows,
        )

    elif (
        noise_model is not None
        and star_model is not None
        and roi_model is None
        and cellpose_model_3D_pretrained_file is None
    ):

        return (
            sized_smart_seeds,
            instance_labels,
            star_labels,
            probability_map,
            markers,
            skeleton,
            image,
        )

    elif (
        noise_model is not None
        and star_model is not None
        and roi_model is None
        and cellpose_model_3D_pretrained_file is not None
    ):

        return (
            sized_smart_seeds,
            instance_labels,
            star_labels,
            probability_map,
            markers,
            skeleton,
            image,
            voll_cell_seg,
            flows,
        )

    # If the stardist model is not supplied but only the unet and noise model we return the denoised result and the semantic segmentation map
    elif (
        star_model is None
        and roi_model is not None
        and noise_model is not None
        and cellpose_model_3D_pretrained_file is None
    ):

        return instance_labels, skeleton, image

    elif (
        star_model is None
        and roi_model is not None
        and noise_model is None
        and cellpose_model_3D_pretrained_file is None
    ):

        return roi_image.astype("uint16"), skeleton, image

    elif (
        star_model is None
        and roi_model is not None
        and noise_model is not None
        and cellpose_model_3D_pretrained_file is None
    ):

        return roi_image.astype("uint16"), skeleton, image

    elif (
        noise_model is not None
        and star_model is None
        and roi_model is None
        and unet_model is None
        and cellpose_model_3D_pretrained_file is None
    ):

        return instance_labels, skeleton, image

    elif (
        star_model is None
        and roi_model is None
        and noise_model is None
        and unet_model is not None
        and cellpose_model_3D_pretrained_file is None
    ):

        return instance_labels, skeleton, image


def _cellpose_3D_star_block(
    cellpose_model_3D_pretrained_file,
    image_membrane,
    image_nuclei,
    unet_model,
    star_model,
    roi_model,
    patch_size,
    in_channels,
    out_activation,
    network_type,
    norm_method,
    background_weight,
    flow_weight,
    out_channels,
    feat_channels,
    num_levels,
    overlap,
    crop,
    ExpandLabels,
    axes,
    noise_model,
    prob_thresh,
    nms_thresh,
    donormalize,
    n_tiles,
    UseProbability,
    dounet,
    seedpool,
    iou_threshold,
    lower_perc,
    upper_perc,
    min_size_mask,
    min_size,
    max_size,
):

    cellres = None
    res = None

    if star_model is not None:

        res = VollSeg3D(
            image_nuclei,
            unet_model,
            star_model,
            roi_model=roi_model,
            ExpandLabels=ExpandLabels,
            axes=axes,
            noise_model=noise_model,
            prob_thresh=prob_thresh,
            nms_thresh=nms_thresh,
            donormalize=donormalize,
            lower_perc=lower_perc,
            upper_perc=upper_perc,
            min_size_mask=min_size_mask,
            min_size=min_size,
            max_size=max_size,
            n_tiles=n_tiles,
            UseProbability=UseProbability,
            dounet=dounet,
            seedpool=seedpool,
            slice_merge=False,
            iou_threshold=iou_threshold,
        )

    if cellpose_model_3D_pretrained_file is not None:
        cellres = _apply_cellpose_network_3D(
            cellpose_model_3D_pretrained_file,
            image_membrane,
            patch_size=patch_size,
            in_channels=in_channels,
            out_activation=out_activation,
            network_type=network_type,
            norm_method=norm_method,
            background_weight=background_weight,
            flow_weight=flow_weight,
            out_channels=out_channels,
            feat_channels=feat_channels,
            num_levels=num_levels,
            overlap=overlap,
            crop=crop,
        )

    return cellres, res


def _cellpose_star_block(
    cellpose_model,
    custom_cellpose_model,
    cellpose_model_name,
    image_membrane,
    image_nuclei,
    diameter_cellpose,
    flow_threshold,
    cellprob_threshold,
    stitch_threshold,
    anisotropy,
    pretrained_cellpose_model_path,
    gpu,
    unet_model,
    star_model,
    roi_model,
    ExpandLabels,
    axes,
    noise_model,
    prob_thresh,
    nms_thresh,
    donormalize,
    n_tiles,
    UseProbability,
    dounet,
    seedpool,
    slice_merge,
    iou_threshold,
    lower_perc,
    upper_perc,
    min_size_mask,
    min_size,
    max_size,
    do_3D,
):

    cellres = None
    res = None

    if star_model is not None:

        res = VollSeg3D(
            image_nuclei,
            unet_model,
            star_model,
            roi_model=roi_model,
            ExpandLabels=ExpandLabels,
            axes=axes,
            noise_model=noise_model,
            prob_thresh=prob_thresh,
            nms_thresh=nms_thresh,
            donormalize=donormalize,
            lower_perc=lower_perc,
            upper_perc=upper_perc,
            min_size_mask=min_size_mask,
            min_size=min_size,
            max_size=max_size,
            n_tiles=n_tiles,
            UseProbability=UseProbability,
            dounet=dounet,
            seedpool=seedpool,
            slice_merge=slice_merge,
            iou_threshold=iou_threshold,
        )

    if cellpose_model is not None:

        if custom_cellpose_model:
            cellpose_model = models.Cellpose(
                gpu=gpu, model_type=cellpose_model_name
            )
            if anisotropy is not None:
                cellres = cellpose_model.eval(
                    image_membrane,
                    diameter=diameter_cellpose,
                    flow_threshold=flow_threshold,
                    cellprob_threshold=cellprob_threshold,
                    stitch_threshold=stitch_threshold,
                    anisotropy=anisotropy,
                    tile=True,
                    do_3D=do_3D,
                )
            else:
                cellres = cellpose_model.eval(
                    image_membrane,
                    diameter=diameter_cellpose,
                    flow_threshold=flow_threshold,
                    cellprob_threshold=cellprob_threshold,
                    stitch_threshold=stitch_threshold,
                    tile=True,
                    do_3D=do_3D,
                )

        else:
            cellpose_model = models.CellposeModel(
                gpu=gpu, pretrained_model=pretrained_cellpose_model_path
            )
            if anisotropy is not None:
                cellres = cellpose_model.eval(
                    image_membrane,
                    diameter=diameter_cellpose,
                    flow_threshold=flow_threshold,
                    cellprob_threshold=cellprob_threshold,
                    stitch_threshold=stitch_threshold,
                    anisotropy=anisotropy,
                    tile=True,
                    do_3D=do_3D,
                )
            else:
                cellres = cellpose_model.eval(
                    image_membrane,
                    diameter=diameter_cellpose,
                    flow_threshold=flow_threshold,
                    cellprob_threshold=cellprob_threshold,
                    stitch_threshold=stitch_threshold,
                    tile=True,
                    do_3D=do_3D,
                )

    return cellres, res


def VollCellSeg(
    image: np.ndarray,
    diameter_cellpose: float = 34.6,
    stitch_threshold: float = 0.5,
    channel_membrane: int = 0,
    channel_nuclei: int = 1,
    flow_threshold: float = 0.4,
    cellprob_threshold: float = 0.0,
    anisotropy=None,
    star_model=None,
    unet_model=None,
    roi_model=None,
    noise_model=None,
    cellpose_model=None,
    custom_cellpose_model: bool = False,
    pretrained_cellpose_model_path: str = None,
    cellpose_model_name="cyto2",
    gpu: bool = False,
    axes: str = "ZYX",
    prob_thresh: float = None,
    ExpandLabels: bool = False,
    nms_thresh: float = None,
    min_size_mask: int = 10,
    min_size: int = 10,
    max_size: int = 10000,
    n_tiles: tuple = (1, 1, 1),
    UseProbability: bool = True,
    donormalize: bool = True,
    lower_perc: float = 1.0,
    upper_perc: float = 99.8,
    dounet: bool = True,
    seedpool: bool = True,
    save_dir: str = None,
    Name: str = "Result",
    slice_merge: bool = False,
    iou_threshold: float = 0.3,
    do_3D: bool = False,
    z_thresh: int = 2,
):

    if prob_thresh is None and nms_thresh is None:
        prob_thresh = star_model.thresholds.prob
        nms_thresh = star_model.thresholds.nms

    if len(image.shape) == 3 and "T" not in axes:
        # Just a 3D image
        image_membrane = image
        image_nuclei = image

        cellres, res = _cellpose_star_block(
            cellpose_model,
            custom_cellpose_model,
            cellpose_model_name,
            image_membrane,
            image_nuclei,
            diameter_cellpose,
            flow_threshold,
            cellprob_threshold,
            stitch_threshold,
            anisotropy,
            pretrained_cellpose_model_path,
            gpu,
            unet_model,
            star_model,
            roi_model,
            ExpandLabels,
            axes,
            noise_model,
            prob_thresh,
            nms_thresh,
            donormalize,
            n_tiles,
            UseProbability,
            dounet,
            seedpool,
            slice_merge,
            iou_threshold,
            lower_perc,
            upper_perc,
            min_size_mask,
            min_size,
            do_3D,
        )

    if len(image.shape) == 4 and "T" not in axes:
        image_membrane = image[:, channel_membrane, :, :]
        image_nuclei = image[:, channel_nuclei, :, :]

        cellres, res = _cellpose_star_block(
            cellpose_model,
            custom_cellpose_model,
            cellpose_model_name,
            image_membrane,
            image_nuclei,
            diameter_cellpose,
            flow_threshold,
            cellprob_threshold,
            stitch_threshold,
            anisotropy,
            pretrained_cellpose_model_path,
            gpu,
            unet_model,
            star_model,
            roi_model,
            ExpandLabels,
            axes,
            noise_model,
            prob_thresh,
            nms_thresh,
            donormalize,
            n_tiles,
            UseProbability,
            dounet,
            seedpool,
            slice_merge,
            iou_threshold,
            lower_perc,
            upper_perc,
            min_size_mask,
            min_size,
            max_size,
            do_3D,
        )

    if len(image.shape) > 4 and "T" in axes:

        if len(n_tiles) == 4:
            n_tiles = (n_tiles[1], n_tiles[2], n_tiles[3])
        image_membrane = image[:, :, channel_membrane, :, :]
        image_nuclei = image[:, :, channel_nuclei, :, :]
        cellres, res = _cellpose_star_time_block(
            cellpose_model,
            custom_cellpose_model,
            cellpose_model_name,
            image_membrane,
            image_nuclei,
            diameter_cellpose,
            flow_threshold,
            cellprob_threshold,
            stitch_threshold,
            anisotropy,
            pretrained_cellpose_model_path,
            gpu,
            unet_model,
            star_model,
            roi_model,
            ExpandLabels,
            axes,
            noise_model,
            prob_thresh,
            nms_thresh,
            donormalize,
            n_tiles,
            UseProbability,
            dounet,
            seedpool,
            slice_merge,
            iou_threshold,
            lower_perc,
            upper_perc,
            min_size_mask,
            min_size,
            max_size,
            do_3D,
        )

    if cellpose_model is not None and custom_cellpose_model:
        cellpose_labels = cellres[0]
        flows = cellres[1]
    if cellpose_model is not None and not custom_cellpose_model:
        cellpose_labels = cellres[0]
        flows = cellres[1]

    cellpose_labels = np.asarray(cellpose_labels)
    cellpose_labels = CleanCellPose(
        cellpose_mask=cellpose_labels, nms_thresh=nms_thresh, z_thresh=z_thresh
    )
    if "T" in axes:
        for i in range(cellpose_labels.shape[0]):
            for j in range(cellpose_labels.shape[1]):
                cellpose_labels[i, j, :] = remove_small_objects(
                    cellpose_labels[i, j, :].astype("uint16"),
                    min_size=min_size_mask,
                )
                cellpose_labels[i, j, :] = remove_big_objects(
                    cellpose_labels[i, j, :].astype("uint16"),
                    max_size=max_size,
                )
    if "T" not in axes:
        for i in range(cellpose_labels.shape[0]):

            cellpose_labels[i, :] = remove_small_objects(
                cellpose_labels[i, :].astype("uint16"), min_size=min_size_mask
            )
            cellpose_labels[i, :] = remove_big_objects(
                cellpose_labels[i, :].astype("uint16"), max_size=max_size
            )

    cellpose_labels_copy = cellpose_labels.copy()
    if (
        noise_model is None
        and star_model is not None
        and roi_model is not None
        and cellpose_model is None
    ):
        (
            sized_smart_seeds,
            instance_labels,
            star_labels,
            probability_map,
            markers,
            skeleton,
            roi_image,
        ) = res

    if (
        noise_model is None
        and star_model is not None
        and roi_model is not None
        and cellpose_model is not None
    ):
        (
            sized_smart_seeds,
            instance_labels,
            star_labels,
            probability_map,
            markers,
            skeleton,
            roi_image,
        ) = res
        sized_smart_seeds = np.asarray(sized_smart_seeds)
        instance_labels = np.asarray(instance_labels)
        star_labels = np.asarray(star_labels)
        probability_map = np.asarray(probability_map)
        markers = np.asarray(markers)
        skeleton = np.asarray(skeleton)
        roi_image = np.asarray(roi_image)
        voll_cell_seg, voll_cell_prob = _cellpose_block(
            axes,
            sized_smart_seeds,
            flows,
            cellpose_labels_copy,
            nms_thresh,
            z_thresh=z_thresh,
        )

    if (
        noise_model is None
        and star_model is not None
        and roi_model is None
        and cellpose_model is None
    ):
        (
            sized_smart_seeds,
            instance_labels,
            star_labels,
            probability_map,
            markers,
            skeleton,
        ) = res

    if (
        noise_model is None
        and star_model is not None
        and roi_model is None
        and cellpose_model is not None
    ):
        (
            sized_smart_seeds,
            instance_labels,
            star_labels,
            probability_map,
            markers,
            skeleton,
        ) = res
        sized_smart_seeds = np.asarray(sized_smart_seeds)
        instance_labels = np.asarray(instance_labels)
        star_labels = np.asarray(star_labels)
        probability_map = np.asarray(probability_map)
        markers = np.asarray(markers)
        skeleton = np.asarray(skeleton)
        voll_cell_seg, voll_cell_prob = _cellpose_block(
            axes,
            sized_smart_seeds,
            flows,
            cellpose_labels_copy,
            nms_thresh,
            z_thresh=z_thresh,
        )

    if (
        noise_model is not None
        and star_model is not None
        and roi_model is not None
        and cellpose_model is None
    ):
        (
            sized_smart_seeds,
            instance_labels,
            star_labels,
            probability_map,
            markers,
            skeleton,
            image,
            roi_image,
        ) = res

    if (
        noise_model is not None
        and star_model is not None
        and roi_model is not None
        and cellpose_model is not None
    ):
        (
            sized_smart_seeds,
            instance_labels,
            star_labels,
            probability_map,
            markers,
            skeleton,
            image,
            roi_image,
        ) = res
        sized_smart_seeds = np.asarray(sized_smart_seeds)
        instance_labels = np.asarray(instance_labels)
        star_labels = np.asarray(star_labels)
        probability_map = np.asarray(probability_map)
        markers = np.asarray(markers)
        skeleton = np.asarray(skeleton)
        image = np.asarray(image)
        roi_image = np.asarray(roi_image)
        voll_cell_seg, voll_cell_prob = _cellpose_block(
            axes,
            sized_smart_seeds,
            flows,
            cellpose_labels_copy,
            nms_thresh,
            z_thresh=z_thresh,
        )

    if (
        noise_model is not None
        and star_model is not None
        and roi_model is None
        and cellpose_model is not None
    ):
        (
            sized_smart_seeds,
            instance_labels,
            star_labels,
            probability_map,
            markers,
            skeleton,
            image,
        ) = res
        (
            sized_smart_seeds,
            instance_labels,
            star_labels,
            probability_map,
            markers,
            skeleton,
            roi_image,
        ) = res
        sized_smart_seeds = np.asarray(sized_smart_seeds)
        instance_labels = np.asarray(instance_labels)
        star_labels = np.asarray(star_labels)
        probability_map = np.asarray(probability_map)
        markers = np.asarray(markers)
        skeleton = np.asarray(skeleton)
        roi_image = np.asarray(roi_image)
        voll_cell_seg, voll_cell_prob = _cellpose_block(
            axes,
            sized_smart_seeds,
            flows,
            cellpose_labels_copy,
            nms_thresh,
            z_thresh=z_thresh,
        )

    if (
        noise_model is None
        and star_model is not None
        and roi_model is not None
        and cellpose_model is not None
    ):

        (
            sized_smart_seeds,
            instance_labels,
            star_labels,
            probability_map,
            markers,
            skeleton,
            roi_image,
        ) = res
        sized_smart_seeds = np.asarray(sized_smart_seeds)
        instance_labels = np.asarray(instance_labels)
        star_labels = np.asarray(star_labels)
        probability_map = np.asarray(probability_map)
        markers = np.asarray(markers)
        skeleton = np.asarray(skeleton)
        roi_image = np.asarray(roi_image)
        voll_cell_seg, voll_cell_prob = _cellpose_block(
            axes,
            sized_smart_seeds,
            flows,
            cellpose_labels_copy,
            nms_thresh,
            z_thresh=z_thresh,
        )

    elif (
        noise_model is not None
        and star_model is None
        and roi_model is None
        and unet_model is None
        and cellpose_model is not None
    ):

        instance_labels, skeleton, image = res

    elif (
        star_model is None
        and roi_model is None
        and unet_model is not None
        and noise_model is not None
        and cellpose_model is not None
    ):

        instance_labels, skeleton, image = res

    elif (
        star_model is None
        and roi_model is not None
        and unet_model is not None
        and noise_model is not None
        and cellpose_model is not None
    ):

        instance_labels, skeleton, image = res

    elif (
        star_model is None
        and roi_model is None
        and unet_model is not None
        and noise_model is None
        and cellpose_model is not None
    ):

        instance_labels, skeleton, image = res

    elif (
        star_model is None
        and roi_model is not None
        and unet_model is None
        and noise_model is None
        and cellpose_model is not None
    ):

        roi_image, skeleton, image = res
        instance_labels = roi_image

    elif (
        star_model is None
        and roi_model is not None
        and unet_model is None
        and noise_model is not None
        and cellpose_model is not None
    ):

        roi_image, skeleton, image = res
        instance_labels = roi_image

    elif (
        star_model is None
        and roi_model is not None
        and unet_model is not None
        and noise_model is None
        and cellpose_model is not None
    ):

        roi_image, skeleton, image = res
        instance_labels = roi_image

    if save_dir is not None:
        print("Saving Results ...")
        Path(save_dir).mkdir(exist_ok=True)

        if cellpose_model is not None:
            cellpose_results = save_dir + "CellPose/"
            Path(cellpose_results).mkdir(exist_ok=True)
            imwrite(
                (cellpose_results + Name + ".tif"),
                np.asarray(cellpose_labels).astype("uint16"),
            )

            vollcellpose_results = save_dir + "VollCellPose/"
            Path(vollcellpose_results).mkdir(exist_ok=True)
            imwrite(
                (vollcellpose_results + Name + ".tif"),
                np.asarray(voll_cell_seg).astype("uint16"),
            )
            if star_model is not None:
                probability_membrane_results = (
                    save_dir + "Probability_membrane_cellpose/"
                )
                Path(probability_membrane_results).mkdir(exist_ok=True)
                imwrite(
                    (probability_membrane_results + Name + ".tif"),
                    np.asarray(voll_cell_prob).astype("float32"),
                )

        if roi_model is not None:
            roi_results = save_dir + "Roi/"
            Path(roi_results).mkdir(exist_ok=True)
            imwrite(
                (roi_results + Name + ".tif"),
                np.asarray(roi_image).astype("uint16"),
            )

        if unet_model is not None:
            unet_results = save_dir + "BinaryMask/"
            skel_unet_results = save_dir + "skeleton/"
            Path(unet_results).mkdir(exist_ok=True)
            Path(skel_unet_results).mkdir(exist_ok=True)

            imwrite(
                (unet_results + Name + ".tif"),
                np.asarray(instance_labels).astype("uint16"),
            )
            imwrite(
                (skel_unet_results + Name + ".tif"),
                np.asarray(skeleton).astype("uint16"),
            )
        if star_model is not None:
            vollseg_results = save_dir + "VollSeg/"
            stardist_results = save_dir + "StarDist/"
            probability_results = save_dir + "Probability/"
            marker_results = save_dir + "markers/"
            skel_results = save_dir + "skeleton/"
            Path(skel_results).mkdir(exist_ok=True)
            Path(vollseg_results).mkdir(exist_ok=True)
            Path(stardist_results).mkdir(exist_ok=True)
            Path(probability_results).mkdir(exist_ok=True)
            Path(marker_results).mkdir(exist_ok=True)
            imwrite(
                (stardist_results + Name + ".tif"),
                np.asarray(star_labels).astype("uint16"),
            )
            imwrite(
                (vollseg_results + Name + ".tif"),
                np.asarray(sized_smart_seeds).astype("uint16"),
            )
            imwrite(
                (probability_results + Name + ".tif"),
                np.asarray(probability_map).astype("float32"),
            )
            imwrite(
                (marker_results + Name + ".tif"),
                np.asarray(markers).astype("uint16"),
            )
            imwrite((skel_results + Name + ".tif"), np.asarray(skeleton))
        if noise_model is not None:
            denoised_results = save_dir + "Denoised/"
            Path(denoised_results).mkdir(exist_ok=True)
            imwrite(
                (denoised_results + Name + ".tif"),
                np.asarray(image).astype("float32"),
            )

    # If denoising is not done but stardist and unet models are supplied we return the stardist, vollseg and semantic segmentation maps
    if (
        noise_model is None
        and star_model is not None
        and roi_model is not None
        and cellpose_model is None
    ):

        return (
            sized_smart_seeds,
            instance_labels,
            star_labels,
            probability_map,
            markers,
            skeleton,
            roi_image,
        )

    if (
        noise_model is None
        and star_model is not None
        and roi_model is not None
        and cellpose_model is not None
    ):

        return (
            sized_smart_seeds,
            instance_labels,
            star_labels,
            probability_map,
            markers,
            skeleton,
            roi_image,
            cellpose_labels,
            voll_cell_seg,
        )

    elif (
        noise_model is None
        and star_model is not None
        and roi_model is None
        and cellpose_model is None
    ):

        return (
            sized_smart_seeds,
            instance_labels,
            star_labels,
            probability_map,
            markers,
            skeleton,
        )

    elif (
        noise_model is None
        and star_model is not None
        and roi_model is None
        and cellpose_model is not None
    ):

        return (
            sized_smart_seeds,
            instance_labels,
            star_labels,
            probability_map,
            markers,
            skeleton,
            cellpose_labels,
            voll_cell_seg,
        )

    # If denoising is done and stardist and unet models are supplied we return the stardist, vollseg, denoised image and semantic segmentation maps
    elif (
        noise_model is not None
        and star_model is not None
        and roi_model is not None
        and cellpose_model is None
    ):

        return (
            sized_smart_seeds,
            instance_labels,
            star_labels,
            probability_map,
            markers,
            skeleton,
            image,
            roi_image,
        )

    elif (
        noise_model is not None
        and star_model is not None
        and roi_model is not None
        and cellpose_model is not None
    ):

        return (
            sized_smart_seeds,
            instance_labels,
            star_labels,
            probability_map,
            markers,
            skeleton,
            image,
            roi_image,
            cellpose_labels,
            voll_cell_seg,
        )

    elif (
        noise_model is not None
        and star_model is not None
        and roi_model is None
        and cellpose_model is None
    ):

        return (
            sized_smart_seeds,
            instance_labels,
            star_labels,
            probability_map,
            markers,
            skeleton,
            image,
        )

    elif (
        noise_model is not None
        and star_model is not None
        and roi_model is None
        and cellpose_model is not None
    ):

        return (
            sized_smart_seeds,
            instance_labels,
            star_labels,
            probability_map,
            markers,
            skeleton,
            image,
            cellpose_labels,
            voll_cell_seg,
        )

    # If the stardist model is not supplied but only the unet and noise model we return the denoised result and the semantic segmentation map
    elif (
        star_model is None
        and roi_model is not None
        and noise_model is not None
        and cellpose_model is None
    ):

        return instance_labels, skeleton, image

    elif (
        star_model is None
        and roi_model is not None
        and noise_model is None
        and cellpose_model is None
    ):

        return roi_image.astype("uint16"), skeleton, image

    elif (
        star_model is None
        and roi_model is not None
        and noise_model is not None
        and cellpose_model is None
    ):

        return roi_image.astype("uint16"), skeleton, image

    elif (
        noise_model is not None
        and star_model is None
        and roi_model is None
        and unet_model is None
        and cellpose_model is None
    ):

        return instance_labels, skeleton, image

    elif (
        star_model is None
        and roi_model is None
        and noise_model is None
        and unet_model is not None
        and cellpose_model is None
    ):

        return instance_labels, skeleton, image


def _cellpose_3D_block(
    axes, sized_smart_seeds, foreground, flows, nms_thresh, z_thresh=1
):

    if "T" not in axes:

        voll_cell_seg = CellPose3DWater(
            sized_smart_seeds,
            foreground,
            flows,
            nms_thresh,
            z_thresh=z_thresh,
        )
    if "T" in axes:

        voll_cell_seg = []
        for time in range(sized_smart_seeds.shape[0]):

            cellpose_flows_time = flows[time]
            cellpose_foreground_time = foreground[time]
            voll_cell_seg_time = CellPose3DWater(
                sized_smart_seeds[time],
                cellpose_foreground_time,
                cellpose_flows_time,
                nms_thresh,
                z_thresh=z_thresh,
            )
            voll_cell_seg.append(voll_cell_seg_time)
        voll_cell_seg = np.asarray(voll_cell_seg_time)

    return voll_cell_seg


def _cellpose_block(
    axes, sized_smart_seeds, flows, cellpose_labels, nms_thresh, z_thresh=1
):

    if "T" not in axes:

        cellpose_base = np.max(flows[0], axis=-1)
        voll_cell_seg = CellPoseWater(
            cellpose_labels,
            sized_smart_seeds,
            cellpose_base,
            nms_thresh,
            z_thresh=z_thresh,
        )
    if "T" in axes:

        cellpose_base = []
        voll_cell_seg = []
        for time in range(cellpose_labels.shape[0]):

            cellpose_labels_time = cellpose_labels[time]
            cellpose_base_time = np.max(flows[0], axis=-1)[time]
            voll_cell_seg_time = CellPoseWater(
                cellpose_labels_time,
                sized_smart_seeds[time],
                cellpose_base_time,
                nms_thresh,
                z_thresh=z_thresh,
            )
            voll_cell_seg.append(voll_cell_seg_time)
        cellpose_base = np.asarray(cellpose_base)
        voll_cell_seg = np.asarray(voll_cell_seg_time)

    return voll_cell_seg, cellpose_base


def VollSeg(
    image,
    unet_model=None,
    star_model=None,
    roi_model=None,
    axes="ZYX",
    noise_model=None,
    prob_thresh=None,
    ExpandLabels=False,
    nms_thresh=None,
    min_size_mask=100,
    min_size=100,
    max_size=10000000,
    n_tiles=(1, 1, 1),
    UseProbability=True,
    donormalize=True,
    lower_perc=1,
    upper_perc=99.8,
    dounet=True,
    seedpool=True,
    save_dir=None,
    Name="Result",
    slice_merge=False,
    iou_threshold=0.3,
    RGB=False,
):

    if len(image.shape) == 2:

        # if the default tiling of the function is not changed by the user, we use the last two tuples
        if len(n_tiles) == 3:
            n_tiles = (n_tiles[1], n_tiles[2])

        # If stardist model is supplied we use this method
        if star_model is not None:

            res = VollSeg2D(
                image,
                unet_model,
                star_model,
                roi_model=roi_model,
                ExpandLabels=ExpandLabels,
                noise_model=noise_model,
                prob_thresh=prob_thresh,
                nms_thresh=nms_thresh,
                donormalize=donormalize,
                lower_perc=lower_perc,
                upper_perc=upper_perc,
                axes=axes,
                min_size_mask=min_size_mask,
                min_size=min_size,
                max_size=max_size,
                dounet=dounet,
                n_tiles=n_tiles,
                UseProbability=UseProbability,
                RGB=RGB,
            )

        # If there is no stardist model we use unet model or denoising model or both to get the semantic segmentation
        if star_model is None:

            res = VollSeg_unet(
                image,
                unet_model=unet_model,
                roi_model=roi_model,
                ExpandLabels=ExpandLabels,
                n_tiles=n_tiles,
                axes=axes,
                min_size_mask=min_size_mask,
                max_size=max_size,
                noise_model=noise_model,
                RGB=RGB,
                iou_threshold=iou_threshold,
                slice_merge=slice_merge,
                dounet=dounet,
            )
    if len(image.shape) == 3 and "T" not in axes and RGB is False:
        # this is a 3D image and if stardist model is supplied we use this method
        if star_model is not None:
            res = VollSeg3D(
                image,
                unet_model,
                star_model,
                roi_model=roi_model,
                ExpandLabels=ExpandLabels,
                axes=axes,
                noise_model=noise_model,
                prob_thresh=prob_thresh,
                nms_thresh=nms_thresh,
                donormalize=donormalize,
                lower_perc=lower_perc,
                upper_perc=upper_perc,
                min_size_mask=min_size_mask,
                min_size=min_size,
                max_size=max_size,
                n_tiles=n_tiles,
                UseProbability=UseProbability,
                dounet=dounet,
                seedpool=seedpool,
                slice_merge=slice_merge,
                iou_threshold=iou_threshold,
            )

        # If there is no stardist model we use unet model with or without denoising model
        if star_model is None:

            res = VollSeg_unet(
                image,
                unet_model=unet_model,
                roi_model=roi_model,
                ExpandLabels=ExpandLabels,
                n_tiles=n_tiles,
                axes=axes,
                min_size_mask=min_size_mask,
                max_size=max_size,
                noise_model=noise_model,
                RGB=RGB,
                iou_threshold=iou_threshold,
                slice_merge=slice_merge,
                dounet=dounet,
            )
    if len(image.shape) == 3 and "T" not in axes and RGB:
        # this is a 3D image and if stardist model is supplied we use this method
        if star_model is not None:
            res = VollSeg2D(
                image,
                unet_model,
                star_model,
                roi_model=roi_model,
                ExpandLabels=ExpandLabels,
                noise_model=noise_model,
                prob_thresh=prob_thresh,
                nms_thresh=nms_thresh,
                donormalize=donormalize,
                lower_perc=lower_perc,
                upper_perc=upper_perc,
                axes=axes,
                min_size_mask=min_size_mask,
                min_size=min_size,
                max_size=max_size,
                dounet=dounet,
                n_tiles=n_tiles,
                UseProbability=UseProbability,
                RGB=RGB,
            )
        # If there is no stardist model we use unet model with or without denoising model
        if star_model is None:

            res = VollSeg_unet(
                image,
                unet_model=unet_model,
                roi_model=roi_model,
                ExpandLabels=ExpandLabels,
                n_tiles=n_tiles,
                axes=axes,
                min_size_mask=min_size_mask,
                max_size=max_size,
                noise_model=noise_model,
                RGB=RGB,
                iou_threshold=iou_threshold,
                slice_merge=slice_merge,
                dounet=dounet,
            )

    if len(image.shape) == 3 and "T" in axes:
        if len(n_tiles) == 3:
            n_tiles = (n_tiles[1], n_tiles[2])
        if star_model is not None:
            res = tuple(
                zip(
                    *tuple(
                        VollSeg2D(
                            _x,
                            unet_model,
                            star_model,
                            noise_model=noise_model,
                            ExpandLabels=ExpandLabels,
                            roi_model=roi_model,
                            prob_thresh=prob_thresh,
                            nms_thresh=nms_thresh,
                            donormalize=donormalize,
                            lower_perc=lower_perc,
                            upper_perc=upper_perc,
                            axes=axes,
                            min_size_mask=min_size_mask,
                            min_size=min_size,
                            max_size=max_size,
                            dounet=dounet,
                            n_tiles=n_tiles,
                            UseProbability=UseProbability,
                            RGB=RGB,
                        )
                        for _x in tqdm(image)
                    )
                )
            )
        if star_model is None:

            res = tuple(
                zip(
                    *tuple(
                        VollSeg_unet(
                            _x,
                            unet_model=unet_model,
                            roi_model=roi_model,
                            ExpandLabels=ExpandLabels,
                            n_tiles=n_tiles,
                            axes=axes,
                            noise_model=noise_model,
                            RGB=RGB,
                            iou_threshold=iou_threshold,
                            slice_merge=slice_merge,
                            dounet=dounet,
                        )
                        for _x in tqdm(image)
                    )
                )
            )

    if len(image.shape) == 4:
        if len(n_tiles) == 4:
            n_tiles = (n_tiles[1], n_tiles[2], n_tiles[3])
        res = tuple(
            zip(
                *tuple(
                    VollSeg3D(
                        _x,
                        unet_model,
                        star_model,
                        axes=axes,
                        noise_model=noise_model,
                        roi_model=roi_model,
                        ExpandLabels=ExpandLabels,
                        prob_thresh=prob_thresh,
                        nms_thresh=nms_thresh,
                        donormalize=donormalize,
                        lower_perc=lower_perc,
                        upper_perc=upper_perc,
                        min_size_mask=min_size_mask,
                        min_size=min_size,
                        max_size=max_size,
                        n_tiles=n_tiles,
                        UseProbability=UseProbability,
                        dounet=dounet,
                        seedpool=seedpool,
                        slice_merge=slice_merge,
                        iou_threshold=iou_threshold,
                    )
                    for _x in tqdm(image)
                )
            )
        )

    if (
        noise_model is None
        and star_model is not None
        and roi_model is not None
    ):
        (
            sized_smart_seeds,
            instance_labels,
            star_labels,
            probability_map,
            markers,
            skeleton,
            roi_image,
        ) = res

    if noise_model is None and star_model is not None and roi_model is None:
        (
            sized_smart_seeds,
            instance_labels,
            star_labels,
            probability_map,
            markers,
            skeleton,
        ) = res

    elif (
        noise_model is not None
        and star_model is not None
        and roi_model is not None
    ):
        (
            sized_smart_seeds,
            instance_labels,
            star_labels,
            probability_map,
            markers,
            skeleton,
            image,
            roi_image,
        ) = res

    elif (
        noise_model is not None
        and star_model is not None
        and roi_model is None
    ):
        (
            sized_smart_seeds,
            instance_labels,
            star_labels,
            probability_map,
            markers,
            skeleton,
            image,
        ) = res

    elif (
        noise_model is not None
        and star_model is None
        and roi_model is None
        and unet_model is None
    ):

        instance_labels, skeleton, image = res

    elif (
        star_model is None
        and roi_model is None
        and unet_model is not None
        and noise_model is not None
    ):

        instance_labels, skeleton, image = res

    elif (
        star_model is None
        and roi_model is not None
        and unet_model is not None
        and noise_model is not None
    ):

        instance_labels, skeleton, image = res

    elif (
        star_model is None
        and roi_model is None
        and unet_model is not None
        and noise_model is None
    ):

        instance_labels, skeleton, image = res

    elif (
        star_model is None
        and roi_model is not None
        and unet_model is None
        and noise_model is None
    ):

        roi_image, skeleton, image = res
        instance_labels = roi_image

    elif (
        star_model is None
        and roi_model is not None
        and unet_model is None
        and noise_model is not None
    ):

        roi_image, skeleton, image = res
        instance_labels = roi_image

    elif (
        star_model is None
        and roi_model is not None
        and unet_model is not None
        and noise_model is None
    ):

        roi_image, skeleton, image = res
        instance_labels = roi_image

    if save_dir is not None:
        print("Saving Results ...")
        Path(save_dir).mkdir(exist_ok=True)

        if roi_model is not None:
            roi_results = save_dir + "Roi/"
            Path(roi_results).mkdir(exist_ok=True)
            imwrite(
                (roi_results + Name + ".tif"),
                np.asarray(roi_image).astype("uint16"),
            )

        if unet_model is not None:
            unet_results = save_dir + "BinaryMask/"
            skel_unet_results = save_dir + "skeleton/"
            Path(unet_results).mkdir(exist_ok=True)
            Path(skel_unet_results).mkdir(exist_ok=True)

            imwrite(
                (unet_results + Name + ".tif"),
                np.asarray(instance_labels).astype("uint16"),
            )
            imwrite(
                (skel_unet_results + Name + ".tif"),
                np.asarray(skeleton).astype("uint16"),
            )
        if star_model is not None:
            vollseg_results = save_dir + "VollSeg/"
            stardist_results = save_dir + "StarDist/"
            probability_results = save_dir + "Probability/"
            marker_results = save_dir + "markers/"
            skel_results = save_dir + "skeleton/"
            Path(skel_results).mkdir(exist_ok=True)
            Path(vollseg_results).mkdir(exist_ok=True)
            Path(stardist_results).mkdir(exist_ok=True)
            Path(probability_results).mkdir(exist_ok=True)
            Path(marker_results).mkdir(exist_ok=True)
            imwrite(
                (stardist_results + Name + ".tif"),
                np.asarray(star_labels).astype("uint16"),
            )
            imwrite(
                (vollseg_results + Name + ".tif"),
                np.asarray(sized_smart_seeds).astype("uint16"),
            )
            imwrite(
                (probability_results + Name + ".tif"),
                np.asarray(probability_map).astype("float32"),
            )
            imwrite(
                (marker_results + Name + ".tif"),
                np.asarray(markers).astype("uint16"),
            )
            imwrite((skel_results + Name + ".tif"), np.asarray(skeleton))
        if noise_model is not None:
            denoised_results = save_dir + "Denoised/"
            Path(denoised_results).mkdir(exist_ok=True)
            imwrite(
                (denoised_results + Name + ".tif"),
                np.asarray(image).astype("float32"),
            )

    # If denoising is not done but stardist and unet models are supplied we return the stardist, vollseg and semantic segmentation maps
    if (
        noise_model is None
        and star_model is not None
        and roi_model is not None
    ):

        return (
            sized_smart_seeds,
            instance_labels,
            star_labels,
            probability_map,
            markers,
            skeleton,
            roi_image,
        )

    elif noise_model is None and star_model is not None and roi_model is None:

        return (
            sized_smart_seeds,
            instance_labels,
            star_labels,
            probability_map,
            markers,
            skeleton,
        )

    # If denoising is done and stardist and unet models are supplied we return the stardist, vollseg, denoised image and semantic segmentation maps
    elif (
        noise_model is not None
        and star_model is not None
        and roi_model is not None
    ):

        return (
            sized_smart_seeds,
            instance_labels,
            star_labels,
            probability_map,
            markers,
            skeleton,
            image,
            roi_image,
        )

    elif (
        noise_model is not None
        and star_model is not None
        and roi_model is None
    ):

        return (
            sized_smart_seeds,
            instance_labels,
            star_labels,
            probability_map,
            markers,
            skeleton,
            image,
        )

    # If the stardist model is not supplied but only the unet and noise model we return the denoised result and the semantic segmentation map
    elif (
        star_model is None
        and roi_model is not None
        and noise_model is not None
    ):

        return instance_labels, skeleton, image

    elif star_model is None and roi_model is not None and noise_model is None:

        return roi_image.astype("uint16"), skeleton, image

    elif (
        star_model is None
        and roi_model is not None
        and noise_model is not None
    ):

        return roi_image.astype("uint16"), skeleton, image

    elif (
        noise_model is not None
        and star_model is None
        and roi_model is None
        and unet_model is None
    ):

        return instance_labels, skeleton, image

    elif (
        star_model is None
        and roi_model is None
        and noise_model is None
        and unet_model is not None
    ):

        return instance_labels, skeleton, image


def VollSeg3D(
    image,
    unet_model,
    star_model,
    axes="ZYX",
    noise_model=None,
    roi_model=None,
    prob_thresh=None,
    nms_thresh=None,
    min_size_mask=100,
    min_size=100,
    max_size=10000000,
    n_tiles=(1, 2, 2),
    UseProbability=True,
    ExpandLabels=True,
    dounet=True,
    seedpool=True,
    donormalize=True,
    lower_perc=1,
    upper_perc=99.8,
    slice_merge=False,
    iou_threshold=0.3,
):

    print("Generating VollSeg results")
    sizeZ = image.shape[0]
    sizeY = image.shape[1]
    sizeX = image.shape[2]
    if len(n_tiles) >= len(image.shape):
        n_tiles = (n_tiles[-3], n_tiles[-2], n_tiles[-1])
    else:
        tiles = n_tiles
    instance_labels = np.zeros([sizeZ, sizeY, sizeX], dtype="uint16")

    sized_smart_seeds = np.zeros([sizeZ, sizeY, sizeX], dtype="uint16")
    sized_probability_map = np.zeros([sizeZ, sizeY, sizeX], dtype="float32")
    sized_markers = np.zeros([sizeZ, sizeY, sizeX], dtype="uint16")
    sized_stardist = np.zeros([sizeZ, sizeY, sizeX], dtype="uint16")
    Mask = None
    Mask_patch = None
    roi_image = None
    if noise_model is not None:
        print("Denoising Image")

        image = noise_model.predict(
            image.astype("float32"), axes=axes, n_tiles=n_tiles
        )
        pixel_condition = image < 0
        pixel_replace_condition = 0
        image = image_conditionals(
            image, pixel_condition, pixel_replace_condition
        )

    if roi_model is not None:

        print("Creating overall mask for the tissue")
        model_dim = roi_model.config.n_dim
        if model_dim < len(image.shape):
            if len(n_tiles) >= len(image.shape):
                tiles = (n_tiles[-2], n_tiles[-1])
            else:
                tiles = n_tiles
            maximage = np.amax(image, axis=0)
            Segmented = roi_model.predict(
                maximage.astype("float32"), "YX", n_tiles=tiles
            )
            try:
                thresholds = threshold_multiotsu(Segmented, classes=2)

                # Using the threshold values, we generate the three regions.
                regions = np.digitize(Segmented, bins=thresholds)
            except ValueError:

                regions = Segmented

            roi_image = regions > 0
            roi_image = label(roi_image)
            roi_bbox = Bbox_region(roi_image)
            if roi_bbox is not None:
                rowstart = roi_bbox[0]
                colstart = roi_bbox[1]
                endrow = roi_bbox[2]
                endcol = roi_bbox[3]
                region = (
                    slice(0, image.shape[0]),
                    slice(rowstart, endrow),
                    slice(colstart, endcol),
                )
            else:
                region = (
                    slice(0, image.shape[0]),
                    slice(0, image.shape[1]),
                    slice(0, image.shape[2]),
                )
                rowstart = 0
                colstart = 0
                endrow = image.shape[2]
                endcol = image.shape[1]
                roi_bbox = [colstart, rowstart, endcol, endrow]
        elif model_dim == len(image.shape):
            Segmented = roi_model.predict(
                maximage.astype("float32"), "YX", n_tiles=n_tiles
            )
            try:
                thresholds = threshold_multiotsu(Segmented, classes=2)

                # Using the threshold values, we generate the three regions.
                regions = np.digitize(Segmented, bins=thresholds)
            except ValueError:

                regions = Segmented

            roi_image = regions > 0
            roi_image = label(roi_image)
            roi_bbox = Bbox_region(roi_image)
            if roi_bbox is not None:
                zstart = roi_bbox[0]
                rowstart = roi_bbox[1]
                colstart = roi_bbox[2]
                zend = roi_bbox[3]
                endrow = roi_bbox[4]
                endcol = roi_bbox[5]
                region = (
                    slice(zstart, zend),
                    slice(rowstart, endrow),
                    slice(colstart, endcol),
                )
            else:

                region = (
                    slice(0, image.shape[0]),
                    slice(0, image.shape[1]),
                    slice(0, image.shape[2]),
                )
                rowstart = 0
                colstart = 0
                endrow = image.shape[2]
                endcol = image.shape[1]
                roi_bbox = [colstart, rowstart, endcol, endrow]

        # The actual pixels in that region.
        if roi_bbox is not None:
            patch = image[region]

        else:
            patch = image

    else:

        patch = image

        region = (
            slice(0, image.shape[0]),
            slice(0, image.shape[1]),
            slice(0, image.shape[2]),
        )
        rowstart = 0
        colstart = 0
        endrow = image.shape[2]
        endcol = image.shape[1]
        roi_bbox = [colstart, rowstart, endcol, endrow]

    if dounet:

        if unet_model is not None:
            print("UNET segmentation on Image", patch.shape)

            Mask = UNETPrediction3D(
                patch,
                unet_model,
                n_tiles,
                axes,
                iou_threshold=iou_threshold,
                slice_merge=slice_merge,
                ExpandLabels=ExpandLabels,
            )
            for i in range(0, Mask.shape[0]):
                Mask[i] = remove_small_objects(
                    Mask[i].astype("uint16"), min_size=min_size_mask
                )
                Mask[i] = remove_big_objects(
                    Mask[i].astype("uint16"), max_size=max_size
                )
            Mask_patch = Mask.copy()
            Mask = Region_embedding(image, roi_bbox, Mask)
            if slice_merge:
                Mask = match_labels(
                    Mask.astype("uint16"), iou_threshold=iou_threshold
                )
            else:
                Mask = label(Mask > 0)
            instance_labels[:, : Mask.shape[1], : Mask.shape[2]] = Mask

    elif noise_model is not None and dounet is False:

        Mask = np.zeros(patch.shape)

        for i in range(0, Mask.shape[0]):

            try:
                thresholds = threshold_multiotsu(patch[i, :], classes=2)

                # Using the threshold values, we generate the three regions.
                regions = np.digitize(patch[i], bins=thresholds)

            except ValueError:

                regions = patch[i]
            Mask[i] = regions > 0
            Mask[i] = label(Mask[i, :])

            Mask[i] = remove_small_objects(
                Mask[i].astype("uint16"), min_size=min_size_mask
            )
            Mask[i] = remove_big_objects(
                Mask[i].astype("uint16"), max_size=max_size
            )
        if slice_merge:
            Mask = match_labels(Mask, iou_threshold=iou_threshold)
        else:
            Mask = label(Mask > 0)
        Mask_patch = Mask.copy()
        Mask = Region_embedding(image, roi_bbox, Mask)
        instance_labels[:, : Mask.shape[1], : Mask.shape[2]] = Mask
    if star_model is not None:
        print("Stardist segmentation on Image")
        if donormalize:

            patch_star = normalize(
                patch, lower_perc, upper_perc, axis=(0, 1, 2)
            )
        else:
            patch_star = patch

        smart_seeds, probability_map, star_labels, markers = STARPrediction3D(
            patch_star,
            axes,
            star_model,
            n_tiles,
            unet_mask=Mask_patch,
            UseProbability=UseProbability,
            seedpool=seedpool,
            prob_thresh=prob_thresh,
            nms_thresh=nms_thresh,
        )
        print("Removing small/large objects")
        for i in tqdm(range(0, smart_seeds.shape[0])):
            smart_seeds[i] = remove_small_objects(
                smart_seeds[i, :].astype("uint16"), min_size=min_size
            )
            smart_seeds[i] = remove_big_objects(
                smart_seeds[i, :].astype("uint16"), max_size=max_size
            )
        smart_seeds = fill_label_holes(smart_seeds.astype("uint16"))

        smart_seeds = Region_embedding(image, roi_bbox, smart_seeds)
        sized_smart_seeds[
            :, : smart_seeds.shape[1], : smart_seeds.shape[2]
        ] = smart_seeds
        markers = Region_embedding(image, roi_bbox, markers)
        sized_markers[
            :, : smart_seeds.shape[1], : smart_seeds.shape[2]
        ] = markers
        probability_map = Region_embedding(image, roi_bbox, probability_map)
        sized_probability_map[
            :, : probability_map.shape[1], : probability_map.shape[2]
        ] = probability_map
        star_labels = Region_embedding(image, roi_bbox, star_labels)
        sized_stardist[
            :, : star_labels.shape[1], : star_labels.shape[2]
        ] = star_labels
        skeleton = np.zeros_like(sized_smart_seeds)
        for i in range(0, sized_smart_seeds.shape[0]):
            skeleton[i] = SmartSkel(
                sized_smart_seeds[i], sized_probability_map[i]
            )
        skeleton = skeleton > 0

    if (
        noise_model is None
        and roi_image is not None
        and star_model is not None
    ):
        return (
            sized_smart_seeds.astype("uint16"),
            instance_labels.astype("uint16"),
            star_labels.astype("uint16"),
            sized_probability_map,
            markers.astype("uint16"),
            skeleton.astype("uint16"),
            roi_image.astype("uint16"),
        )
    if noise_model is None and roi_image is None and star_model is not None:
        return (
            sized_smart_seeds.astype("uint16"),
            instance_labels.astype("uint16"),
            star_labels.astype("uint16"),
            sized_probability_map,
            markers.astype("uint16"),
            skeleton.astype("uint16"),
        )
    if (
        noise_model is not None
        and roi_image is None
        and star_model is not None
    ):
        return (
            sized_smart_seeds.astype("uint16"),
            instance_labels.astype("uint16"),
            star_labels.astype("uint16"),
            sized_probability_map,
            markers.astype("uint16"),
            skeleton.astype("uint16"),
            image,
        )
    if (
        noise_model is not None
        and roi_image is not None
        and star_model is not None
    ):
        return (
            sized_smart_seeds.astype("uint16"),
            instance_labels.astype("uint16"),
            star_labels.astype("uint16"),
            sized_probability_map,
            markers.astype("uint16"),
            skeleton.astype("uint16"),
            image,
            roi_image.astype("uint16"),
        )

    if (
        noise_model is not None
        and roi_image is not None
        and star_model is None
    ):
        return instance_labels.astype("uint16"), skeleton, image

    if (
        noise_model is not None
        and roi_image is None
        and star_model is None
        and unet_model is None
    ):
        return instance_labels.astype("uint16"), skeleton, image

    if (
        noise_model is None
        and roi_image is None
        and star_model is None
        and unet_model is not None
    ):
        return instance_labels.astype("uint16"), skeleton, image


def image_pixel_duplicator(image, size):

    assert len(image.shape) == len(
        size
    ), f"The provided size {len(size)} should match the image dimensions {len(image.shape)}"

    model_dim = len(size)

    if model_dim == 3:
        size_y = size[0]
        size_x = size[1]
        size_z = size[2]
        if size_y <= image.shape[0]:
            size_y = image.shape[0]
        if size_x <= image.shape[1]:
            size_x = image.shape[1]
        if size_z <= image.shape[2]:
            size_z = image.shape[2]

        size = (size_y, size_x, size_z)
        ResizeImage = np.zeros(size)
        j = 0
        for i in range(0, ResizeImage.shape[1]):

            if j < image.shape[1]:
                ResizeImage[: image.shape[0], i, : image.shape[2]] = image[
                    : image.shape[0], j, : image.shape[2]
                ]
                j = j + 1
            else:
                j = 0

        j = 0
        for i in range(0, ResizeImage.shape[2]):

            if j < image.shape[2]:
                ResizeImage[:, :, i] = ResizeImage[:, :, j]
                j = j + 1
            else:
                j = 0

        j = 0
        for i in range(0, ResizeImage.shape[0]):

            if j < image.shape[0]:
                ResizeImage[i, :, :] = ResizeImage[j, :, :]
                j = j + 1
            else:
                j = 0

    if model_dim == 2:

        size_y = size[0]
        size_x = size[1]
        if size_y <= image.shape[0]:
            size_y = image.shape[0]
        if size_x <= image.shape[1]:
            size_x = image.shape[1]

        size = (size_y, size_x)

        ResizeImage = np.zeros(size)
        j = 0
        for i in range(0, ResizeImage.shape[1]):

            if j < image.shape[1]:
                ResizeImage[: image.shape[0], i] = image[: image.shape[0], j]
                j = j + 1
            else:
                j = 0

        j = 0
        for i in range(0, ResizeImage.shape[0]):

            if j < image.shape[0]:
                ResizeImage[i, :] = ResizeImage[j, :]
                j = j + 1
            else:
                j = 0

    return ResizeImage


def image_conditionals(image, pixel_condition, pixel_replace_condition):

    indices = zip(*np.where(pixel_condition))
    for index in indices:

        image[index] = pixel_replace_condition

    return image


def image_embedding(image, size):

    model_dim = len(image.shape)
    if model_dim == 2:
        assert len(image.shape) == len(
            size
        ), f"The provided size {len(size)} should match the image dimensions {len(image.shape)}"
        for i in range(len(size)):
            assert (
                image.shape[i] <= size[i]
            ), f"The image size should be smaller \
            than the volume it is to be embedded in but found image of size {image.shape[i]} for dimension{i}"
            width = []
            for i in range(len(size)):
                width.append(size[i] - image.shape[i])
            width = np.asarray(width)

            ResizeImage = np.pad(image, width, "constant", constant_values=0)
    if model_dim == 3:
        ResizeImage = []
        width = []
        for i in range(len(size)):
            width.append(size[i] - image.shape[i + 1])
        width = np.asarray(width)
        for i in range(image.shape[0]):

            ResizeImage.append(
                np.pad(image[i, :], width, "constant", constant_values=0)
            )
        ResizeImage = np.asarray(ResizeImage)
    return ResizeImage


def Integer_to_border(Label):

    BoundaryLabel = find_boundaries(Label, mode="outer")

    Binary = BoundaryLabel > 0

    return Binary


def SuperUNETPrediction(image, model, n_tiles, axis):

    Segmented = model.predict(image.astype("float32"), axis, n_tiles=n_tiles)

    try:
        thresholds = threshold_multiotsu(Segmented, classes=2)

        # Using the threshold values, we generate the three regions.
        regions = np.digitize(Segmented, bins=thresholds)
    except ValueError:

        regions = Segmented

    Binary = regions > 0
    Finalimage = label(Binary)

    Finalimage = relabel_sequential(Finalimage)[0]

    return Finalimage


def merge_labels_across_volume(labelvol, relabelfunc, threshold=3):
    nz, ny, nx = labelvol.shape
    res = np.zeros_like(labelvol)
    res[0, ...] = labelvol[0, ...]
    backup = labelvol.copy()  # kapoors code modifies the input array
    for i in tqdm(range(nz - 1)):

        res[i + 1, ...] = relabelfunc(
            res[i, ...], labelvol[i + 1, ...], threshold=threshold
        )
        labelvol = backup.copy()  # restore the input array
    res = res.astype("uint16")
    return res


def RelabelZ(previousImage, currentImage, threshold):

    currentImage = currentImage.astype("uint16")
    relabelimage = currentImage
    previousImage = previousImage.astype("uint16")
    waterproperties = measure.regionprops(previousImage)
    indices = [prop.centroid for prop in waterproperties]
    if len(indices) > 0:
        tree = spatial.cKDTree(indices)
        currentwaterproperties = measure.regionprops(currentImage)
        currentindices = [prop.centroid for prop in currentwaterproperties]
        if len(currentindices) > 0:
            for i in range(0, len(currentindices)):
                index = currentindices[i]
                currentlabel = currentImage[int(index[0]), int(index[1])]
                if currentlabel > 0:
                    previouspoint = tree.query(index)
                    previouslabel = previousImage[
                        int(indices[previouspoint[1]][0]),
                        int(indices[previouspoint[1]][1]),
                    ]
                    if previouspoint[0] > threshold:

                        pixel_condition = currentImage == currentlabel
                        pixel_replace_condition = currentlabel
                        relabelimage = image_conditionals(
                            relabelimage,
                            pixel_condition,
                            pixel_replace_condition,
                        )

                    else:
                        pixel_condition = currentImage == currentlabel
                        pixel_replace_condition = previouslabel
                        relabelimage = image_conditionals(
                            relabelimage,
                            pixel_condition,
                            pixel_replace_condition,
                        )

    return relabelimage


def CleanMask(star_labels, OverAllunet_mask):
    OverAllunet_mask = np.logical_or(OverAllunet_mask > 0, star_labels > 0)
    OverAllunet_mask = binary_erosion(OverAllunet_mask)
    OverAllunet_mask = label(OverAllunet_mask)
    OverAllunet_mask = fill_label_holes(OverAllunet_mask.astype("uint16"))

    return OverAllunet_mask


def UNETPrediction3D(
    image,
    model,
    n_tiles,
    axis,
    iou_threshold=0.3,
    min_size_mask=10,
    max_size=100000,
    slice_merge=False,
    erosion_iterations=15,
    ExpandLabels=True,
):

    model_dim = model.config.n_dim

    if model_dim < len(image.shape):
        Segmented = np.zeros_like(image)

        for i in range(image.shape[0]):
            Segmented[i] = model.predict(
                image[i].astype("float32"),
                axis.replace("Z", ""),
                n_tiles=(n_tiles[-2], n_tiles[-1]),
            )

    else:

        Segmented = model.predict(
            image.astype("float32"), axis, n_tiles=n_tiles
        )

    try:
        thresholds = threshold_multiotsu(Segmented, classes=2)

        # Using the threshold values, we generate the three regions.
        regions = np.digitize(Segmented, bins=thresholds)
    except ValueError:

        regions = Segmented

    Binary = regions > 0
    overall_mask = Binary.copy()

    if model_dim == 3:
        for i in range(image.shape[0]):
            overall_mask[i] = binary_dilation(
                overall_mask[i], iterations=erosion_iterations
            )
            overall_mask[i] = binary_erosion(
                overall_mask[i], iterations=erosion_iterations
            )
            overall_mask[i] = fill_label_holes(overall_mask[i])

    Binary = label(Binary)

    if model_dim == 2:
        Binary = remove_small_objects(
            Binary.astype("uint16"), min_size=min_size_mask
        )
        Binary = remove_big_objects(Binary.astype("uint16"), max_size=max_size)
        Binary = fill_label_holes(Binary)
        Finalimage = relabel_sequential(Binary)[0]
        skeleton = Skel(Finalimage)
        skeleton = skeleton > 0
    if model_dim == 3 and slice_merge:
        for i in range(image.shape[0]):
            Binary[i] = label(Binary[i])

        Binary = match_labels(Binary, iou_threshold=iou_threshold)
        Binary = fill_label_holes(Binary)

    if model_dim == 3:
        for i in range(image.shape[0]):
            Binary[i] = remove_small_objects(
                Binary[i].astype("uint16"), min_size=min_size_mask
            )
            Binary[i] = remove_big_objects(
                Binary[i].astype("uint16"), max_size=max_size
            )
        Finalimage = relabel_sequential(Binary)[0]
        skeleton = Skel(Finalimage)

        if ExpandLabels:

            Finalimage, skeleton = VollSeg_label_expansion(
                image, overall_mask, Finalimage, skeleton
            )

    return Finalimage


def Bbox_region(image):

    props = measure.regionprops(image)
    area = [prop.area for prop in props]
    if len(area) > 0:
        largest_blob_ind = np.argmax(area)
        largest_bbox = props[largest_blob_ind].bbox
        return largest_bbox


def SuperSTARPrediction(
    image,
    model,
    n_tiles,
    unet_mask=None,
    OverAllunet_mask=None,
    UseProbability=True,
    prob_thresh=None,
    nms_thresh=None,
    seedpool=True,
):

    if prob_thresh is None and nms_thresh is None:
        prob_thresh = model.thresholds.prob
        nms_thresh = model.thresholds.nms

    if prob_thresh is not None and nms_thresh is not None:

        star_labels, SmallProbability, SmallDistance = model.predict_vollseg(
            image.astype("float32"),
            n_tiles=n_tiles,
            prob_thresh=prob_thresh,
            nms_thresh=nms_thresh,
        )
    else:
        star_labels, SmallProbability, SmallDistance = model.predict_vollseg(
            image.astype("float32"), n_tiles=n_tiles
        )

    grid = model.config.grid
    Probability = resize(
        SmallProbability,
        output_shape=(
            SmallProbability.shape[0] * grid[0],
            SmallProbability.shape[1] * grid[1],
        ),
    )
    Distance = MaxProjectDist(SmallDistance, axis=-1)
    Distance = resize(
        Distance,
        output_shape=(
            Distance.shape[0] * grid[0],
            Distance.shape[1] * grid[1],
        ),
    )

    pixel_condition = Probability < GLOBAL_THRESH
    pixel_replace_condition = 0
    Probability = image_conditionals(
        Probability, pixel_condition, pixel_replace_condition
    )

    if UseProbability:

        MaxProjectDistance = Probability[
            : star_labels.shape[0], : star_labels.shape[1]
        ]

    else:

        MaxProjectDistance = Distance[
            : star_labels.shape[0], : star_labels.shape[1]
        ]

    if OverAllunet_mask is None:
        OverAllunet_mask = unet_mask
    if OverAllunet_mask is not None:
        OverAllunet_mask = CleanMask(star_labels, OverAllunet_mask)

    if unet_mask is None:
        unet_mask = star_labels > 0
    Watershed, markers = SuperWatershedwithMask(
        MaxProjectDistance,
        star_labels.astype("uint16"),
        unet_mask.astype("uint16"),
        nms_thresh=nms_thresh,
        seedpool=seedpool,
    )
    Watershed = fill_label_holes(Watershed.astype("uint16"))

    return Watershed, markers, star_labels, MaxProjectDistance


def STARPrediction3D(
    image,
    axes,
    model,
    n_tiles,
    unet_mask=None,
    UseProbability=True,
    seedpool=True,
    prob_thresh=None,
    nms_thresh=None,
):

    copymodel = model

    grid = copymodel.config.grid
    print("Predicting Instances")
    if prob_thresh is None and nms_thresh is None:
        prob_thresh = model.thresholds.prob
        nms_thresh = model.thresholds.nms
    if prob_thresh is not None and nms_thresh is not None:

        print(
            f"Using user choice of prob_thresh = {prob_thresh} and nms_thresh = {nms_thresh}"
        )

        if prob_thresh is not None and nms_thresh is not None:

            (
                star_labels,
                SmallProbability,
                SmallDistance,
            ) = model.predict_vollseg(
                image.astype("float32"),
                axes=axes,
                n_tiles=n_tiles,
                prob_thresh=prob_thresh,
                nms_thresh=nms_thresh,
            )
        else:
            (
                star_labels,
                SmallProbability,
                SmallDistance,
            ) = model.predict_vollseg(
                image.astype("float32"), axes=axes, n_tiles=n_tiles
            )

    print("Predictions Done")

    if UseProbability is False:

        SmallDistance = MaxProjectDist(SmallDistance, axis=-1)
        Distance = np.zeros(
            [
                SmallDistance.shape[0] * grid[0],
                SmallDistance.shape[1] * grid[1],
                SmallDistance.shape[2] * grid[2],
            ]
        )

    Probability = np.zeros(
        [
            SmallProbability.shape[0] * grid[0],
            SmallProbability.shape[1] * grid[1],
            SmallProbability.shape[2] * grid[2],
        ]
    )

    # We only allow for the grid parameter to be 1 along the Z axis
    for i in range(0, SmallProbability.shape[0]):
        Probability[i, :] = resize(
            SmallProbability[i, :],
            output_shape=(Probability.shape[1], Probability.shape[2]),
        )

        if UseProbability is False:
            Distance[i, :] = resize(
                SmallDistance[i, :],
                output_shape=(Distance.shape[1], Distance.shape[2]),
            )

    if UseProbability:

        print("Using Probability maps")
        MaxProjectDistance = Probability[
            : star_labels.shape[0],
            : star_labels.shape[1],
            : star_labels.shape[2],
        ]

    else:

        print("Using Distance maps")
        MaxProjectDistance = Distance[
            : star_labels.shape[0],
            : star_labels.shape[1],
            : star_labels.shape[2],
        ]

    print("Doing Watershedding")

    if unet_mask is None:
        unet_mask = star_labels > 0

    Watershed, markers = WatershedwithMask3D(
        MaxProjectDistance,
        star_labels.astype("uint16"),
        unet_mask.astype("uint16"),
        nms_thresh=nms_thresh,
        seedpool=seedpool,
    )
    Watershed = fill_label_holes(Watershed.astype("uint16"))

    return Watershed, MaxProjectDistance, star_labels, markers


def SuperWatershedwithMask(
    Image, Label, mask, nms_thresh, seedpool, z_thresh=1
):

    CopyImage = Image.copy()
    properties = measure.regionprops(Label)
    Coordinates = [prop.centroid for prop in properties]
    binaryproperties = measure.regionprops(label(mask), CopyImage)
    BinaryCoordinates = [prop.centroid for prop in binaryproperties]
    Binarybbox = [prop.bbox for prop in binaryproperties]

    Starbbox = [prop.bbox for prop in properties]
    Starlabel = [prop.label for prop in properties]
    if len(Starbbox) > 0:
        for i in range(0, len(Starbbox)):

            box = Starbbox[i]
            starlabel = Starlabel[i]
            include = [
                UnetStarMask(box, unet).masking() for unet in BinaryCoordinates
            ]
            if False not in include:
                indices = zip(*np.where(Label == starlabel))
                for index in indices:

                    mask[index] = 1

    binaryproperties = measure.regionprops(label(mask))
    BinaryCoordinates = [prop.centroid for prop in binaryproperties]
    Binarybbox = [prop.bbox for prop in binaryproperties]
    if seedpool:
        if len(Binarybbox) > 0:
            for i in range(0, len(Binarybbox)):

                box = Binarybbox[i]
                include = [
                    SeedPool(box, star).pooling() for star in Coordinates
                ]

                if False not in include:
                    Coordinates.append(BinaryCoordinates[i])
    Coordinates.append((0, 0))
    Coordinates = np.asarray(Coordinates)

    coordinates_int = np.round(Coordinates).astype(int)
    markers_raw = np.zeros_like(CopyImage)
    markers_raw[tuple(coordinates_int.T)] = 1 + np.arange(len(Coordinates))

    markers = morphology.dilation(markers_raw, morphology.disk(2))
    watershedImage = watershed(-CopyImage, markers, mask=mask.copy())

    watershedImage = NMSLabel(
        watershedImage, nms_thresh, z_thresh=z_thresh
    ).supressregions()

    return watershedImage, markers


def CleanCellPose(cellpose_mask, nms_thresh, z_thresh=1):

    cellpose_mask_copy = cellpose_mask.copy()
    cellpose_mask_copy = NMSLabel(
        cellpose_mask_copy, nms_thresh, z_thresh=z_thresh
    ).supressregions()

    return cellpose_mask_copy


def CellPose3DWater(
    sized_smart_seeds, foreground, flows, nms_thresh, z_thresh=1
):

    Copyflows = flows.copy()
    CopyMasks = sized_smart_seeds.copy()
    starproperties = measure.regionprops(CopyMasks)
    KeepCoordinates = [prop.centroid for prop in starproperties]
    KeepCoordinates = np.asarray(KeepCoordinates)

    coordinates_int = np.round(KeepCoordinates).astype(int)
    markers_raw = np.zeros_like(sized_smart_seeds)
    markers_raw[tuple(coordinates_int.T)] = 1 + np.arange(len(KeepCoordinates))

    markers = morphology.dilation(
        markers_raw.astype("uint16"), morphology.ball(2)
    )
    watershed_image_nuclei = watershed(Copyflows, markers, mask=foreground)
    watershed_image_nuclei = fill_label_holes(watershed_image_nuclei)
    watershed_image_nuclei = dilate_label_holes(
        watershed_image_nuclei, iterations=1
    )

    relabeled = NMSLabel(
        watershed_image_nuclei, nms_thresh, z_thresh=z_thresh
    ).supressregions()
    relabeled = fill_label_holes(relabeled)
    return relabeled


def CellPoseWater(
    cellpose_mask, sized_smart_seeds, cellpose_base, nms_thresh, z_thresh=1
):

    Copycellpose_base = cellpose_base.copy()
    CopyMasks = sized_smart_seeds.copy()
    starproperties = measure.regionprops(CopyMasks)
    Coordinates = [prop.centroid for prop in starproperties]

    properties = measure.regionprops(cellpose_mask)
    bbox = [prop.bbox for prop in properties]
    KeepCoordinates = []
    if len(bbox) > 0:
        for i in range(0, len(Coordinates)):

            star = Coordinates[i]
            include = [UnetStarMask(box, star).semi_masking() for box in bbox]
            if False not in include:
                KeepCoordinates.append(Coordinates[i])

    KeepCoordinates.append((0, 0, 0))
    KeepCoordinates = np.asarray(KeepCoordinates)

    coordinates_int = np.round(KeepCoordinates).astype(int)
    markers_raw = np.zeros_like(Copycellpose_base)
    markers_raw[tuple(coordinates_int.T)] = 1 + np.arange(len(KeepCoordinates))

    thresholds = threshold_multiotsu(Copycellpose_base, classes=2)
    regions = np.digitize(Copycellpose_base, bins=thresholds)
    probability_mask = regions > 0
    probability_mask = binary_erosion(probability_mask, iterations=1)

    probability_mask = binary_fill_holes(probability_mask)

    markers = morphology.dilation(
        markers_raw.astype("uint16"), morphology.ball(2)
    )
    watershed_image_nuclei = watershed(
        Copycellpose_base, markers, mask=probability_mask
    )
    watershed_image_nuclei = fill_label_holes(watershed_image_nuclei)
    watershed_image_nuclei = dilate_label_holes(
        watershed_image_nuclei, iterations=1
    )
    cellpose_mask_copy = cellpose_mask.copy()

    empy_region_indices = zip(*np.where(cellpose_mask_copy == 0))
    for index in empy_region_indices:
        cellpose_mask_copy[index] = watershed_image_nuclei[index]

    cellpose_mask_copy = label(cellpose_mask_copy)
    relabeled = NMSLabel(
        cellpose_mask_copy, nms_thresh, z_thresh=z_thresh
    ).supressregions()
    relabeled = fill_label_holes(relabeled)
    return relabeled


def WatershedwithMask3D(
    Image, Label, mask, nms_thresh, seedpool=True, z_thresh=1
):

    CopyImage = Image.copy()
    properties = measure.regionprops(Label)

    Coordinates = [prop.centroid for prop in properties]
    binaryproperties = measure.regionprops(label(mask))
    BinaryCoordinates = [prop.centroid for prop in binaryproperties]
    Binarybbox = [prop.bbox for prop in binaryproperties]
    Coordinates = sorted(Coordinates, key=lambda k: [k[0], k[1], k[2]])
    Starbbox = [prop.bbox for prop in properties]
    Starlabel = [prop.label for prop in properties]
    if len(Starbbox) > 0:
        for i in range(0, len(Starbbox)):

            box = Starbbox[i]
            starlabel = Starlabel[i]
            include = [
                UnetStarMask(box, unet).masking() for unet in BinaryCoordinates
            ]
            if False not in include:
                indices = zip(*np.where(Label == starlabel))
                for index in indices:
                    mask[index] = 1
    binaryproperties = measure.regionprops(label(mask))
    BinaryCoordinates = [prop.centroid for prop in binaryproperties]
    Binarybbox = [prop.bbox for prop in binaryproperties]

    if seedpool:

        if len(Binarybbox) > 0:
            for i in range(0, len(Binarybbox)):

                box = Binarybbox[i]
                include = [
                    SeedPool(box, star).pooling() for star in Coordinates
                ]

                if False not in include:
                    Coordinates.append(BinaryCoordinates[i])

    Coordinates.append((0, 0, 0))

    Coordinates = np.asarray(Coordinates)
    coordinates_int = np.round(Coordinates).astype(int)

    markers_raw = np.zeros_like(CopyImage)
    markers_raw[tuple(coordinates_int.T)] = 1 + np.arange(len(Coordinates))
    markers = morphology.dilation(
        markers_raw.astype("uint16"), morphology.ball(2)
    )
    watershedImage = watershed(-CopyImage, markers, mask=mask.copy())

    watershedImage = NMSLabel(
        watershedImage, nms_thresh, z_thresh=z_thresh
    ).supressregions()

    return watershedImage, markers


def MaxProjectDist(Image, axis=-1):

    MaxProject = np.amax(Image, axis=axis)

    return MaxProject


def MidProjectDist(Image, axis=-1, slices=1):

    assert len(Image.shape) >= 3
    SmallImage = Image.take(
        indices=range(
            Image.shape[axis] // 2 - slices, Image.shape[axis] // 2 + slices
        ),
        axis=axis,
    )

    MaxProject = np.amax(SmallImage, axis=axis)
    return MaxProject


def normalizeFloatZeroOne(
    x, pmin=3, pmax=99.8, axis=None, eps=1e-20, dtype=np.float32
):
    """Percentile based Normalization
    Normalize patches of image before feeding into the network
    Parameters
    ----------
    x : np array Image patch
    pmin : minimum percentile value for normalization
    pmax : maximum percentile value for normalization
    axis : axis along which the normalization has to be carried out
    eps : avoid dividing by zero
    dtype: type of numpy array, float 32 default
    """
    mi = np.percentile(x, pmin, axis=axis, keepdims=True)
    ma = np.percentile(x, pmax, axis=axis, keepdims=True)
    return normalizer(x, mi, ma, eps=eps, dtype=dtype)


# https://docs.python.org/3/library/itertools.html#itertools-recipes


def normalizeZeroOne(x):

    x = x.astype("float32")

    minVal = np.min(x)
    maxVal = np.max(x)

    x = (x - minVal) / (maxVal - minVal + 1.0e-20)

    return x


def normalizeZero255(x):

    x = x.astype("float32")

    minVal = np.min(x)
    maxVal = np.max(x)

    x = (x - minVal) / (maxVal - minVal + 1.0e-20)

    return x * 255


def normalizer(x, mi, ma, eps=1e-20, dtype=np.float32):
    """
    Number expression evaluation for normalization
    Parameters
    ----------
    x : np array of Image patch
    mi : minimum input percentile value
    ma : maximum input percentile value
    eps: avoid dividing by zero
    dtype: type of numpy array, float 32 defaut
    """

    if dtype is not None:
        x = x.astype(dtype, copy=False)
        mi = dtype(mi) if np.isscalar(mi) else mi.astype(dtype, copy=False)
        ma = dtype(ma) if np.isscalar(ma) else ma.astype(dtype, copy=False)
        eps = dtype(eps)

        x = (x - mi) / (ma - mi + eps)

        x = normalizeZeroOne(x)
    return x


# CARE csbdeep modification of implemented function


def normalizeFloat(
    x, pmin=3, pmax=99.8, axis=None, eps=1e-20, dtype=np.float32
):
    """Percentile based Normalization
    Normalize patches of image before feeding into the network
    Parameters
    ----------
    x : np array Image patch
    pmin : minimum percentile value for normalization
    pmax : maximum percentile value for normalization
    axis : axis along which the normalization has to be carried out
    eps : avoid dividing by zero
    dtype: type of numpy array, float 32 default
    """
    mi = np.percentile(x, pmin, axis=axis, keepdims=True)
    ma = np.percentile(x, pmax, axis=axis, keepdims=True)
    return normalize_mi_ma(x, mi, ma, eps=eps, dtype=dtype)


def normalize_mi_ma(x, mi, ma, eps=1e-20, dtype=np.float32):
    """
    Number expression evaluation for normalization
    Parameters
    ----------
    x : np array of Image patch
    mi : minimum input percentile value
    ma : maximum input percentile value
    eps: avoid dividing by zero
    dtype: type of numpy array, float 32 defaut
    """

    if dtype is not None:
        x = x.astype(dtype, copy=False)
        mi = dtype(mi) if np.isscalar(mi) else mi.astype(dtype, copy=False)
        ma = dtype(ma) if np.isscalar(ma) else ma.astype(dtype, copy=False)
        eps = dtype(eps)

    x = (x - mi) / (ma - mi + eps)

    return x


def plot_train_history(history, savedir, modelname, *keys, **kwargs):
    """Plot (Keras) training history returned by :func:`CARE.train`."""
    import matplotlib.pyplot as plt

    logy = kwargs.pop("logy", False)

    if all(isinstance(k, str) for k in keys):
        w, keys = 1, [keys]
    else:
        w = len(keys)

    plt.gcf()
    for i, group in enumerate(keys):
        plt.subplot(1, w, i + 1)
        for k in [group] if isinstance(group, str) else group:
            plt.plot(
                history.epoch, history.history[k], ".-", label=k, **kwargs
            )
            if logy:
                plt.gca().set_yscale("log", nonposy="clip")
        plt.xlabel("epoch")
        plt.legend(loc="best")
    plt.savefig(savedir + "/" + modelname + "train_accuracy" + ".png", dpi=600)
