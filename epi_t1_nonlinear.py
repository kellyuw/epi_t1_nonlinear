#!/usr/bin/python
# -*- coding: utf-8 -*-

from nipype.pipeline.engine import Node, Workflow
from nipype.interfaces import Function
from nipype.utils.filemanip import filename_to_list
import nipype.interfaces.io as nio
import nipype.interfaces.utility as util
import nipype.interfaces.fsl as fsl
import nipype.interfaces.ants as ants
import nipype.interfaces.c3 as c3
import nipype.interfaces.freesurfer as fs

def create_epi_t1_nonlinear_pipeline(name='epi_t1_nonlinear'):
    """Creates a pipeline that performs nonlinear EPI to T1 registration using 
    the antsRegistration tool. Beforehand, the T1 image has to be processed in 
    freesurfer and the EPI timeseries should be realigned.

    Example
    -------

    >>> nipype_epi_t1_nonlin = create_epi_t1_nonlinear_pipeline('nipype_epi_t1_nonlin')
    >>> nipype_epi_t1_nonlin.inputs.inputnode.fs_subject_id = '123456'
    >>> nipype_epi_t1_nonlin.inputs.inputnode.fs_subjects_dir = '/project/data/freesurfer'
    >>> nipype_epi_t1_nonlin.inputs.inputnode.realigned_epi = 'mcflirt.nii.gz'
    >>> nipype_epi_t1_nonlin.run()

    Inputs::

        inputnode.fs_subject_id    # subject id used in freesurfer
        inputnode.fs_subjects_dir  # path to freesurfer output
        inputnode.realigned_epi    # realigned EPI timeseries

    Outputs::

        outputnode.lin_epi2anat     # ITK format
        outputnode.lin_anat2epi     # ITK format
        outputnode.nonlin_epi2anat  # ANTs specific 5D deformation field
        outputnode.nonlin_anat2epi  # ANTs specific 5D deformation field

    """

    nonreg = Workflow(name='epi_t1_nonlinear')
    
    # input
    inputnode = Node(interface=util.IdentityInterface(fields=['fs_subject_id','fs_subjects_dir', 'realigned_epi']),
                     name='inputnode')
                  

    # calculate the temporal mean image of the realigned timeseries
    tmean = Node(interface=fsl.maths.MeanImage(dimension='T',
                                               output_type = 'NIFTI_GZ'), 
                     name='tmean')

    nonreg.connect(inputnode, 'realigned_epi', tmean, 'in_file')


    # import brain.mgz and ribbon.mgz from freesurfer directory
    fs_import = Node(interface=nio.FreeSurferSource(),
                     name = 'freesurfer_import')
    
    nonreg.connect(inputnode, 'fs_subjects_dir', fs_import, 'subjects_dir')
    nonreg.connect(inputnode, 'fs_subject_id', fs_import, 'subject_id')
    
    # convert brain.mgz to niigz
    mriconvert = Node(interface=fs.MRIConvert(out_type='niigz'),
                     name='mriconvert')

    nonreg.connect(fs_import, 'brain', mriconvert, 'in_file')

    # calculate rigid transformation of mean epi to t1 with bbregister
    bbregister = Node(interface=fs.BBRegister(init='fsl', 
                                              contrast_type='t2', 
                                              out_fsl_file = True), 
                     name='bbregister')

    nonreg.connect(inputnode,'fs_subjects_dir', bbregister, 'subjects_dir')
    nonreg.connect(inputnode, 'fs_subject_id', bbregister, 'subject_id')
    nonreg.connect(tmean, 'out_file', bbregister, 'source_file')
    
    # convert linear transformation to itk format compatible with ants
    itk = Node(interface=c3.C3dAffineTool(fsl2ras=True,
                                          itk_transform='epi2anat_affine.txt'), 
                     name='itk')

    nonreg.connect(tmean, 'out_file', itk, 'source_file')
    nonreg.connect(mriconvert, 'out_file', itk, 'reference_file')
    nonreg.connect(bbregister, 'out_fsl_file', itk, 'transform_file')


    # get aparc aseg mask
    # create brainmask from aparc+aseg
    def get_aparc_aseg(files):
        for name in files:
            if 'aparc+aseg' in name:
                return name

    aparc_aseg_mask = Node(fs.Binarize(min=0.1,
                                 dilate=10,
                                 erode=7,
                                 out_type='nii.gz',
                                 binary_file='aparc_aseg_mask.nii.gz'),
                   name='aparc_aseg_mask')


    # fill holes in mask
    fillholes = Node(fsl.maths.MathsCommand(args='-fillh'),
                     name='fillholes')
    
    nonreg.connect([(fs_import, aparc_aseg_mask, [(('aparc_aseg', get_aparc_aseg), 'in_file')]),
                     (aparc_aseg_mask, fillholes, [('binary_file', 'in_file')])])
    
    
    #create bounding box mask and rigidly transform into anatomical (fs) space
    fov = Node(interface=fs.model.Binarize(min=0.0,
                                                   out_type='nii.gz'),
                     name='fov')

    nonreg.connect(tmean, 'out_file', fov, 'in_file')

    fov_trans = Node(interface=ants.resampling.ApplyTransforms(dimension=3,
                                                                  interpolation='NearestNeighbor'),
                     name='fov_trans')

    nonreg.connect(itk, ('itk_transform',filename_to_list), fov_trans, 'transforms')
    nonreg.connect(fov, 'binary_file', fov_trans, 'input_image')
    nonreg.connect(fillholes, 'out_file', fov_trans, 'reference_image')
    #nonreg.connect(ribbon, 'binary_file', fov_trans, 'reference_image')

    # intersect both masks
    intersect = Node(interface=fsl.maths.BinaryMaths(operation = 'mul'), 
                     name = 'intersect')

    nonreg.connect(fillholes, 'out_file', intersect, 'in_file')
    #nonreg.connect(ribbon, 'binary_file', intersect, 'in_file')
    nonreg.connect(fov_trans, 'output_image', intersect, 'operand_file')

    # inversly transform mask and mask original epi
    mask_trans = Node(interface=ants.resampling.ApplyTransforms(dimension=3,
                                                                   interpolation='NearestNeighbor',
                                                                   invert_transform_flags=[True]), 
                     name = 'mask_trans')

    nonreg.connect(itk, ('itk_transform',filename_to_list), mask_trans, 'transforms')
    nonreg.connect(intersect, 'out_file',  mask_trans, 'input_image')
    nonreg.connect(tmean, 'out_file', mask_trans, 'reference_image')

    maskepi = Node(interface=fs.utils.ApplyMask(), 
                     name='maskepi')

    nonreg.connect(mask_trans, 'output_image', maskepi, 'mask_file')
    nonreg.connect(tmean, 'out_file', maskepi, 'in_file')

    # mask anatomical image (brain)
    maskanat = Node(interface=fs.utils.ApplyMask(), 
                     name='maskanat')

    nonreg.connect(intersect, 'out_file', maskanat, 'mask_file')
    nonreg.connect(mriconvert, 'out_file', maskanat, 'in_file')

    # invert masked anatomical image
    anat_min_max = Node(interface=fsl.utils.ImageStats(op_string = '-R'),
                     name='anat_min_max')
    epi_min_max = Node(interface=fsl.utils.ImageStats(op_string = '-r'), 
                     name='epi_min_max')

    nonreg.connect(maskanat, 'out_file', anat_min_max, 'in_file') 
    nonreg.connect(tmean, 'out_file', epi_min_max, 'in_file') 

    def calc_inversion(anat_min_max, epi_min_max):
        mul = -(epi_min_max[1]-epi_min_max[0])/(anat_min_max[1]-anat_min_max[0])
        add = abs(anat_min_max[1]*mul)+epi_min_max[0]
        return mul, add

    calcinv = Node(interface=Function(input_names=['anat_min_max', 'epi_min_max'],
                                      output_names=['mul', 'add'],
                                      function=calc_inversion),
                     name='calcinv')

    nonreg.connect(anat_min_max, 'out_stat', calcinv, 'anat_min_max')
    nonreg.connect(epi_min_max, 'out_stat', calcinv, 'epi_min_max')

    mulinv = Node(interface=fsl.maths.BinaryMaths(operation='mul'), name='mulinv')
    addinv = Node(interface=fsl.maths.BinaryMaths(operation='add'), name='addinv')

    nonreg.connect(maskanat, 'out_file', mulinv, 'in_file')
    nonreg.connect(calcinv, 'mul', mulinv, 'operand_value')
    nonreg.connect(mulinv, 'out_file', addinv, 'in_file')
    nonreg.connect(calcinv, 'add', addinv, 'operand_value')
    

    # nonlinear transformation of masked anat to masked epi with ants
    antsreg = Node(interface = ants.registration.Registration(dimension = 3,
                                                           invert_initial_moving_transform = True,
                                                           metric = ['CC'],
                                                           metric_weight = [1.0],
                                                           radius_or_number_of_bins = [4],
                                                           sampling_strategy = ['None'],
                                                           transforms = ['SyN'],
                                                           args = '-g .1x1x.1',
                                                           transform_parameters = [(0.10,3,0)],
                                                           number_of_iterations = [[10,5]],
                                                           convergence_threshold = [1e-06],
                                                           convergence_window_size = [10],
                                                           shrink_factors = [[2,1]],
                                                           smoothing_sigmas = [[1,0.5]],
                                                           sigma_units = ['vox'],
                                                           use_estimate_learning_rate_once = [True],
                                                           use_histogram_matching = [True],
                                                           collapse_output_transforms=True,
                                                           output_inverse_warped_image = True,
                                                           output_warped_image = True),
                      name = 'antsreg')

    nonreg.connect(itk, 'itk_transform', antsreg, 'initial_moving_transform')
    nonreg.connect(maskepi, 'out_file', antsreg, 'fixed_image')
    nonreg.connect(addinv, 'out_file', antsreg, 'moving_image')
    
    # output
    
    def second_element(file_list):
        return file_list[1]
    
    def first_element(file_list):
        return file_list[0]
    
    outputnode = Node(interface=util.IdentityInterface(fields=['lin_epi2anat', 'lin_anat2epi',
                                                               'nonlin_epi2anat', 'nonlin_anat2epi']),
                      name = 'outputnode')
    
    nonreg.connect(itk, 'itk_transform', outputnode, 'lin_epi2anat')
    nonreg.connect(antsreg, ('forward_transforms', first_element), outputnode, 'lin_anat2epi')
    nonreg.connect(antsreg, ('forward_transforms', second_element), outputnode, 'nonlin_anat2epi')
    nonreg.connect(antsreg, ('reverse_transforms', second_element), outputnode, 'nonlin_epi2anat')

    return nonreg



import argparse

if __name__=='__main__':
    parser = argparse.ArgumentParser(description='nipype epi_t1_nonlinear workflow for commandline use')
    parser.add_argument("-epi", dest="epi",help="realigned EPI timeseries", required=True)
    parser.add_argument("-fsdir", dest="fsdir",help="path to freesurfer subjects directory",required=True)
    parser.add_argument("-fsid", dest="fsid",help="subject id used in freesurfer",required=True)
    parser.add_argument("-wd", dest="wd",help="working directory to store output",required=True)
    args = parser.parse_args()
    
    nipype_epi_t1_nonlin = create_epi_t1_nonlinear_pipeline('nipype_epi_t1_nonlin')
    nipype_epi_t1_nonlin.base_dir = args.wd
    nipype_epi_t1_nonlin.inputs.inputnode.fs_subject_id = args.fsid
    nipype_epi_t1_nonlin.inputs.inputnode.fs_subjects_dir = args.fsdir
    nipype_epi_t1_nonlin.inputs.inputnode.realigned_epi = args.epi
    nipype_epi_t1_nonlin.run()

