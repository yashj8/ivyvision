# global
import ivy
import math
import ivy_mech
import numpy as np
from operator import mul
from functools import reduce

# local
from ivy_vision import single_view_geometry as _ivy_svg


MIN_DENOMINATOR = 1e-12


def downsampled_image_dims_from_desired_num_pixels(image_dims, num_pixels, maximum=False):
    """
    Compute the best downsampled image dimensions, given original image dimensions and the ideal total number of
    pixels for the downsampled image. The final number of pixels in the downsampled image dimensions may not be exact,
    but will best balance aspect ratio preservation and total number of pixels.  
    
    :param image_dims: The image dimensions of the original image.
    :type image_dims: sequence of ints.
    :param num_pixels: The ideal numbr of pixels in the downsampled image.
    :type num_pixels: int
    :param maximum: Whether the number of pixels is a hard maximum. Default is False.
    :type maximum: bool, optional
    :return: The image dimensions for a new downsampled image.
    """
    int_method = math.floor if maximum else round
    ratio = image_dims[1] / image_dims[0]
    new_dim_0 = (num_pixels / ratio) ** 0.5
    new_dim_1 = int_method(new_dim_0 * ratio)
    new_dim_0 = int_method(new_dim_0)
    new_img_dims = [new_dim_0, new_dim_1]
    num_pixels = new_dim_0 * new_dim_1
    return new_img_dims, num_pixels


def create_sampled_pixel_coords_image(image_dims, samples_per_dim, batch_shape=None, normalized=False, randomize=True,
                                      homogeneous=False, dev_str='cpu'):
    """
    Create image of randomly sampled homogeneous integer :math:`xy` pixel co-ordinates :math:`\mathbf{X}\in\mathbb{Z}^{h×w×3}`,
    stored as floating point values. The origin is at the top-left corner of the image, with :math:`+x` rightwards, and
    :math:`+y` downwards. The final homogeneous dimension are all ones. In subsequent use of this image, the depth of
    each pixel can be represented using this same homogeneous representation, by simply scaling each 3-vector by the
    depth value. The final dimension therefore always holds the depth value, while the former two dimensions hold depth
    scaled pixel co-ordinates.\n
    `[reference] <localhost:63342/ivy/docs/source/references/mvg_textbook.pdf#page=172>`_
    deduction from top of page 154, section 6.1, equation 6.1

    :param image_dims: Image dimensions.
    :type image_dims: sequence of ints.
    :param samples_per_dim: The number of samples to perform per image dimension.
    :type samples_per_dim: sequence of ints.
    :param batch_shape: Shape of batch. Assumed no batch dimensions if None.
    :type batch_shape: sequence of ints, optional
    :param normalized: Whether to normalize x-y pixel co-ordinates to the range 0-1. Default is False.
    :type normalized: bool, optional
    :param randomize: Whether to randomize the sampled co-ordiantes within their window. Default is True.
    :type randomize: bool, optional
    :param homogeneous: Whether the pixel co-ordinates should be 3D homogeneous or just 2D. Default is True.
    :type: homogeneous: bool, optional
    :param dev_str: device on which to create the array 'cuda:0', 'cuda:1', 'cpu' etc.
    :type dev_str: str, optional
    :return: Image of homogeneous pixel co-ordinates *[batch_shape,height,width,3]*
    """

    # BS x DH x DW x 2
    low_res_pix_coords = _ivy_svg.create_uniform_pixel_coords_image(
        samples_per_dim, batch_shape, homogeneous=False, dev_str=dev_str)

    # 2
    window_size =\
        ivy.array(list(reversed([img_dim/sam_per_dim for img_dim, sam_per_dim in zip(image_dims, samples_per_dim)])),
                  dev_str=dev_str)

    # BS x DH x DW x 2
    downsam_pix_coords = low_res_pix_coords * window_size + window_size/2 - 0.5

    if randomize:

        # BS x DH x DW x 1
        rand_x = ivy.random_uniform(
            -window_size[0]/2, window_size[0]/2, list(downsam_pix_coords.shape[:-1]) + [1], dev_str=dev_str)
        rand_y = ivy.random_uniform(
            -window_size[1]/2, window_size[1]/2, list(downsam_pix_coords.shape[:-1]) + [1], dev_str=dev_str)

        # BS x DH x DW x 2
        rand_offsets = ivy.concatenate((rand_x, rand_y), -1)
        downsam_pix_coords += rand_offsets
    downsam_pix_coords = ivy.round(downsam_pix_coords)

    if normalized:
        downsam_pix_coords /=\
            ivy.array([image_dims[1], image_dims[0]], dtype_str='float32', dev_str=dev_str) + MIN_DENOMINATOR

    if homogeneous:
        # BS x DH x DW x 3
        downsam_pix_coords = ivy_mech.make_coordinates_homogeneous(downsam_pix_coords, batch_shape + samples_per_dim)

    # BS x DH x DW x 2or3
    return downsam_pix_coords


def sample_images(list_of_images, num_pixels, batch_shape, image_dims, dev_str='cpu'):
    """
    Samples each image in a list of aligned images at num_pixels random pixel co-ordinates, within a unifrom grid
    over the image.

    :param list_of_images: List of images to sample from.
    :type list_of_images: sequence of arrays.
    :param num_pixels: The number of pixels to sample from each image.
    :type num_pixels: int
    :param batch_shape: Shape of batch. Inferred from inputs if None.
    :type batch_shape: sequence of ints, optional
    :param image_dims: Image dimensions. Inferred from inputs in None.
    :type image_dims: sequence of ints, optional
    :param dev_str: device on which to create the array 'cuda:0', 'cuda:1', 'cpu' etc. Same as images if None.
    :type dev_str: str, optional
    :return:
    """

    if batch_shape is None:
        batch_shape = list_of_images[0].shape[:-3]

    if image_dims is None:
        image_dims = list_of_images[0].shape[-3:-1]

    if dev_str is None:
        dev_str = ivy.dev_str(list_of_images[0])

    image_channels = [img.shape[-1] for img in list_of_images]

    # shapes as list
    batch_shape = list(batch_shape)
    image_dims = list(image_dims)
    flat_batch_size = reduce(mul, batch_shape)

    new_img_dims, num_pixels_to_use = downsampled_image_dims_from_desired_num_pixels(
        image_dims, num_pixels)

    # BS x DH x DW x 2
    sampled_pix_coords = ivy.cast(create_sampled_pixel_coords_image(
        image_dims, new_img_dims, batch_shape, homogeneous=False, dev_str=dev_str), 'int32')

    # FBS x DH x DW x 2
    sampled_pix_coords_flat = ivy.reshape(sampled_pix_coords, [flat_batch_size] + new_img_dims + [2])

    # FBS x DH x DW x 1
    batch_idxs = ivy.expand_dims(ivy.transpose(ivy.cast(ivy.linspace(
        ivy.zeros(new_img_dims, dev_str=dev_str),
        ivy.ones(new_img_dims, dev_str=dev_str) * (flat_batch_size - 1), flat_batch_size, -1),
        'int32'), (2, 0, 1)), -1)

    # FBS x DH x DW x 3
    total_idxs = ivy.concatenate((batch_idxs, ivy.flip(sampled_pix_coords_flat, -1)), -1)

    # list of FBS x H x W x D
    flat_batch_images = [ivy.reshape(img, [flat_batch_size] + image_dims + [-1]) for img in list_of_images]

    # FBS x H x W x sum(D)
    combined_img = ivy.concatenate(flat_batch_images, -1)

    # BS x FID x sum(D)
    combined_img_sampled = ivy.reshape(ivy.gather_nd(combined_img, total_idxs), batch_shape + [num_pixels_to_use, -1])

    # list of BS x FID x D
    return ivy.split(combined_img_sampled, image_channels, -1)


def sinusoid_positional_encoding(x, embedding_length=10):
    """
    Perform sinusoid positional encoding of the inputs.

    :param x: input array to encode *[batch_shape, dim]*
    :type x: array
    :param embedding_length: Length of the embedding. Default is 10.
    :type embedding_length: int, optional
    :return: The new positionally encoded array *[batch_shape, dim+dim*2*embedding_length]*
    """
    rets = [x]
    for i in range(embedding_length):
        for fn in [ivy.sin, ivy.cos]:
            rets.append(fn(2.**i * x))
    return ivy.concatenate(rets, -1)


# noinspection PyUnresolvedReferences
def sampled_volume_density_to_occupancy_probability(density, inter_sample_distance):
    """
    Compute probability of occupancy, given sampled volume densities and their associated inter-sample distances

    :param density: The sampled density values *[batch_shape]*
    :type density: array
    :param inter_sample_distance: The inter-sample distances *[batch_shape]*
    :type inter_sample_distance: array
    :return: The occupancy probabilities *[batch_shape]*
    """
    return 1 - ivy.exp(-density*inter_sample_distance)


def ray_termination_probabilities(density, inter_sample_distance):
    """
    Compute probability of occupancy, given sampled volume densities and their associated inter-sample distances

    :param density: The sampled density values *[batch_shape,num_samples_per_ray]*
    :type density: array
    :param inter_sample_distance: The inter-sample distances *[batch_shape,num_samples_per_ray]*
    :type inter_sample_distance: array
    :return: The occupancy probabilities *[batch_shape]*
    """

    # BS x NSPR
    occ_prob = sampled_volume_density_to_occupancy_probability(density, inter_sample_distance)
    return occ_prob * ivy.cumprod(1. - occ_prob + 1e-10, -1, exclusive=True)


# noinspection PyUnresolvedReferences
def stratified_sample(starts, ends, num_samples, batch_shape=None):
    """
    Perform stratified sampling, between start and end arrays. This operation divides the range into equidistant bins,
    and uniformly samples value within the ranges for each of these bins.

    :param starts: Start values *[batch_shape]*
    :type starts: array
    :param ends: End values *[batch_shape]*
    :type ends: array
    :param num_samples: The number of samples to generate between starts and ends
    :type num_samples: int
    :param batch_shape: Shape of batch, Inferred from inputs if None.
    :type batch_shape: sequence of ints, optional
    :return: The stratified samples, with each randomly placed in uniformly spaced bins *[batch_shape,num_samples]*
    """

    # shapes
    if batch_shape is None:
        batch_shape = starts.shape

    # shapes as lists
    batch_shape = list(batch_shape)

    # BS
    bin_sizes = (ends - starts)/num_samples

    # BS x NS
    linspace_vals = ivy.linspace(starts, ends - bin_sizes, num_samples)

    # BS x NS
    random_uniform = ivy.random_uniform(shape=batch_shape + [num_samples], dev_str=ivy.dev_str(starts))

    # BS x NS
    random_offsets = random_uniform * ivy.expand_dims(bin_sizes, -1)

    # BS x NS
    return linspace_vals + random_offsets


# noinspection PyUnresolvedReferences
def render_rays_via_termination_probabilities(ray_term_probs, features, render_variance=False):
    """
    Render features onto the image plane, given rays sampled at radial depths with readings of
    feature values and densities at these sample points.

    :param ray_term_probs: The ray termination probabilities *[batch_shape,num_samples_per_ray]*
    :type ray_term_probs: array
    :param features: Feature values at the sample points *[batch_shape,num_samples_per_ray,feat_dim]*
    :type features: array
    :param render_variance: Whether to also render the feature variance. Default is False.
    :type render_variance: bool, optional
    :return: The feature renderings along the rays, computed via the termination probabilities *[batch_shape,feat_dim]*
    """

    # BS x NSPR
    rendering = ivy.reduce_sum(ivy.expand_dims(ray_term_probs, -1) * features, -2)
    if not render_variance:
        return rendering
    var = ivy.reduce_sum(ray_term_probs*(ivy.expand_dims(rendering, -2) - features)**2, -2)
    return rendering, var


def render_implicit_features_and_depth(network_fn, rays_o, rays_d, near, far, samples_per_ray, render_variance=False,
                                       batch_size_per_query=512*64, inter_feat_fn=None, v=None):
    """
    Render an rgb-d image, given an implicit rgb and density function conditioned on xyz data.

    :param network_fn: the implicit function.
    :type network_fn: callable
    :param rays_o: The camera center *[batch_shape,3]*
    :type rays_o: array
    :param rays_d: The rays in world space *[batch_shape,ray_batch_shape,3]*
    :type rays_d: array
    :param near: The near clipping plane values *[batch_shape,ray_batch_shape]*
    :type near: array
    :param far: The far clipping plane values *[batch_shape,ray_batch_shape]*
    :type far: array
    :param samples_per_ray: The number of stratified samples to use along each ray
    :type samples_per_ray: int
    :param render_variance: Whether to also render the feature variance. Default is False.
    :type render_variance: bool, optional
    :param batch_size_per_query: The maximum batch size for querying the implicit network. Default is 1024.
    :type batch_size_per_query: int, optional
    :param inter_feat_fn: Function to extract interpolated features from world-coords *[batch_shape,ray_batch_shape,3]*
    :type inter_feat_fn: callable, optional
    :param v: The container of trainable variables for the implicit model. default is to use internal variables.
    :type v: ivy Container of variables
    :return: The rendered feature *[batch_shape,ray_batch_shape,feat]* and radial depth *[batch_shape,ray_batch_shape,1]*
    """

    # shapes
    batch_shape = list(rays_o.shape[:-1])
    num_batch_dims = len(batch_shape)
    ray_batch_shape = list(rays_d.shape[num_batch_dims:-1])
    num_ray_batch_dims = len(ray_batch_shape)
    total_batch_shape = batch_shape + ray_batch_shape
    flat_total_batch_size = np.prod(total_batch_shape)
    num_sections = math.ceil(flat_total_batch_size*samples_per_ray/batch_size_per_query)

    # Compute 3D query points

    # BS x RBS x SPR x 1
    z_vals = ivy.expand_dims(stratified_sample(near, far, samples_per_ray), -1)

    # BS x RBS x 1 x 3
    rays_d = ivy.expand_dims(rays_d, -2)
    rays_o = ivy.broadcast_to(ivy.reshape(rays_o, batch_shape + [1]*(num_ray_batch_dims+1) + [3]), rays_d.shape)

    # BS x RBS x SPR x 3
    pts = rays_o + rays_d * z_vals

    # (BSxRBSxSPR) x 3
    pts_flat = ivy.reshape(pts, (flat_total_batch_size*samples_per_ray, 3))

    # batch
    # ToDo: use a more general batchify function, from ivy core

    # num_sections size list of BSPQ x 3
    pts_split = ivy.split(pts_flat, batch_size_per_query, 0, True)
    if inter_feat_fn is not None:
        # (BSxRBSxSPR) x IF
        features = ivy.reshape(inter_feat_fn(pts), (flat_total_batch_size*samples_per_ray, -1))
        # num_sections size list of BSPQ x IF
        feats_split = ivy.split(features, batch_size_per_query, 0, True)
    else:
        feats_split = [None]*num_sections

    # Run network

    # num_sections size list of tuple of (BSPQ x OF, BSPQ)
    feats_n_densities = [network_fn(pt, f, v=v) for pt, f in zip(pts_split, feats_split)]

    # BS x RBS x SPR x OF
    feat = ivy.reshape(ivy.concatenate([item[0] for item in feats_n_densities], 0),
                       total_batch_shape + [samples_per_ray, -1])

    # BS x RBS x SPR
    densities = ivy.reshape(ivy.concatenate([item[1] for item in feats_n_densities], 0),
                            total_batch_shape + [samples_per_ray])

    # BS x RBS x (SPR+1)
    z_vals_w_terminal = ivy.concatenate((z_vals[..., 0], ivy.ones_like(z_vals[..., -1:, 0])*1e10), -1)

    # BS x RBS x SPR
    depth_diffs = z_vals_w_terminal[..., 1:] - z_vals_w_terminal[..., :-1]
    ray_term_probs = ray_termination_probabilities(densities, depth_diffs)

    # BS x RBS x OF
    feat = ivy.clip(render_rays_via_termination_probabilities(ray_term_probs, feat, render_variance), 0., 1.)

    # BS x RBS x 1
    radial_depth = render_rays_via_termination_probabilities(ray_term_probs, z_vals, render_variance)
    if render_variance:
        # BS x RBS x OF, BS x RBS x OF, BS x RBS x 1, BS x RBS x 1
        return feat[0], feat[1], radial_depth[0], radial_depth[1]
    # BS x RBS x OF, BS x RBS x 1
    return feat, radial_depth