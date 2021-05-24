# VollSeg
3D segmentation tool for irregular shaped cells

![Segmentation](https://github.com/kapoorlab/VollSeg/blob/main/images/Seg_compare-big.png)

[![Build Status](https://travis-ci.com/kapoorlab/vollseg.svg?branch=master)](https://travis-ci.com/github/kapoorlab/vollseg)
[![PyPI version](https://img.shields.io/pypi/v/vollseg.svg?maxAge=2591000)](https://pypi.org/project/vollseg/)
## Installation
This package can be installed by 


`pip install --user vollseg`

If you are building this from the source, clone the repository and install via

```bash
git clone https://github.com/kapoorlab/vollseg/

cd vollseg

pip install --user -e .

# or, to install in editable mode AND grab all of the developer tools
# (this is required if you want to contribute code back to NapaTrackMater)
pip install --user -r requirements.txt
```


### Pipenv install

Pipenv allows you to install dependencies in a virtual environment.

```bash
# install pipenv if you don't already have it installed
pip install --user pipenv

# clone the repository and sync the dependencies
git clone https://github.com/kapoorlab/vollseg/
cd vollseg
pipenv sync

# make the current package available
pipenv run python setup.py develop

# you can run the example notebooks by starting the jupyter notebook inside the virtual env
pipenv run jupyter notebook
```

Access the `example` folder and run the cells.

## Algorithm
![Algorithm](https://github.com/kapoorlab/VollSeg/blob/main/images/Seg_pipe-git.png)

     Schematic representation showing the segmentation approach used in VollSeg.  A) The input is the Raw image of cells in 3D , the image is passed through trained denoising, B) Stardist and C) U-Net networks. In B) we can see the star convex approximation to the cells and in C) is the U-Net prediction labelled via connected components. Having these results we obtain seeds from the centroids of labelled image in B, for each labelled region of C we create bounding boxes and centroids. If there is no seed coming from B in the bounding box region we add the new centroid to the seed pool. In D we have an extra seed (in yellow) coming from U-Net. Using these seeds we do a marker controlled watershed in 3D using skimage implementation on the probability map shown in E) to obtain final cell segmentation result shown in F). All except the image in E) are displayed in Napari viewer with 3D display view. 
     
## Example

To try the provided notebooks we provide an example dataset of MDA231 human breast carcinoma cells infected with a pMSCV vector including the GFP sequence, embedded in a collagen matrix from Dr. R. Kamm. Dept. of Biological Engineering, Massachusetts Institute of Technology, Cambridge MA (USA)[tracking challenge](http://celltrackingchallenge.net/3d-datasets/), download the hyperstacks of the Raw, instance and semantic segmentation masks from [here](https://drive.google.com/drive/folders/1ze8KsrFI0-UTrsMnAPomiyf4sN8aCm__?usp=sharing). Pretrained model weights for denoising done via noise to void, segmentation done via U-Net and Staardist are also in the directory. For training the networks use this notebook in [Colab](https://github.com/kapoorlab/VollSeg/blob/main/examples/ColabTrainModel.ipynb). We provide  pre-trained model weights for stardist and U-Net. To train a denoising model using noise to void use this [notebook](https://github.com/kapoorlab/VollSeg/blob/main/examples/ColabN2VTrain.ipynb) 

## Requirements

- Python 3.7 and above.


## License

Under MIT license. See [LICENSE](LICENSE).

## Authors

- Varun Kapoor <randomaccessiblekapoor@gmail.com>
- Claudia Carabana Garcia
