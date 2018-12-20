import torch
from torch.nn.utils.rnn import PackedSequence
from torch.nn.utils.rnn import pad_packed_sequence
import itertools

from padertorch.ops.tensor import move_axis
from padertorch.ops.einsum import einsum


__all__ = [
    'softmax_cross_entropy',
    'deep_clustering_loss',
    'pit_mse_loss',
    'kl_normal_multivariatenormals'
]


IGNORE_INDEX = -1


def softmax_cross_entropy(x, t):
    """Allow inputs to be of type `PackedSequence`.

    In my understanding, all dimensions but the last should be treated as
    independent dimensions. Therefore, I argue for x.size() == (..., K) where
    t.size() == (...). Similarly, for sequences x.size() == (T, B, ..., K) and
    t.size() == (T, B, ...).

    Check the test case for typical usage.

    Params:
        x: `Tensor` or `PackedSequence` holding a multidimensional array whose
            elements indicate unnormalized log probabilities (logits).
        t: Same object type as `x`. Holds integers of ground truth labels.

    Returns:

    >>> x = torch.randn(100, 3)
    >>> t = torch.randint(0, 3, size=(100,), dtype=torch.long)
    >>> softmax_cross_entropy(x, t).size()
    torch.Size([])
    """
    if isinstance(x, torch.Tensor) and isinstance(t, torch.Tensor):
        pass
    elif isinstance(x, PackedSequence) and isinstance(t, PackedSequence):
        # Data is already organized such that no padding is necessary.
        x, t = x.data, t.data
    else:
        raise ValueError(f'Incompatible types: {type(x)}, {type(t)}')

    assert x.size()[:-1] == t.size(), f'{x.size()}, {t.size()}'
    loss_fn = torch.nn.CrossEntropyLoss(ignore_index=IGNORE_INDEX)
    return loss_fn(move_axis(x, -1, 1), t)


def deep_clustering_loss(x, t):
    """Allows `PackedSequence`.

    The trick to access x.data as in e.g. CE loss does not work, because this
    loss combines knowledge across all time frequency slots.

    Args:
        x: Shape (N, E), where it is assumed that each embedding vector
            is normalized to unit norm.
            Alternatively, packed sequence with data shape (sum_T, F, E).
        t: Target mask with shape (N, K).
            Alternatively, packed sequence with data shape (sum_T, F, K).

    Returns:

    """
    if isinstance(x, torch.Tensor) and isinstance(t, torch.Tensor):
        # This yields losses in the range 10^-2 to 10^0.
        N = x.size()[0]
        return (
            torch.sum(einsum('ne,nE->eE', x, x) ** 2)
            - 2 * torch.sum(einsum('ne,nK->eK', x, t) ** 2)
            + torch.sum(einsum('nk,nK->kK', t, t) ** 2)
        ) // N ** 2
    elif isinstance(x, PackedSequence) and isinstance(t, PackedSequence):
        x, _ = pad_packed_sequence(x)
        t, num_frames = pad_packed_sequence(t)
        return torch.mean(torch.stack([
            deep_clustering_loss(
                x[:num_frames_, b, :, :].view(-1, x.size()[-1]),
                t[:num_frames_, b, :, :].view(-1, t.size()[-1])
            )
            for b, num_frames_ in enumerate(num_frames)
        ]))
    else:
        raise ValueError(f'Incompatible types: {type(x)}, {type(t)}')


def pit_mse_loss(estimate, target):
    """Does not support batch dimension. Does not support PackedSequence.

    TODO: Allow to replace `mse_loss` with other functions.

    Parameters:
        estimate: Padded sequence with shape (T, K, F)
        target: Padded sequence with shape (T, K, F)
    """
    sources = 2  # Replace this later, when you gained more confidence.
    assert estimate.size() == target.size(), (
        f'{estimate.size()} != {target.size()}'
    )
    candidates = []
    for permutation in itertools.permutations(range(sources)):
        candidates.append(torch.nn.functional.mse_loss(
            estimate,
            target[:, permutation, :]
        ))
    return torch.min(torch.stack(candidates))


def _batch_diag(bmat):
    r"""
    Returns the diagonals of a batch of square matrices.
    """
    return bmat.reshape(bmat.shape[:-2] + (-1,))[..., ::bmat.size(-1) + 1]


def _batch_inverse(bmat):
    r"""
    Returns the inverses of a batch of square matrices.
    """
    n = bmat.size(-1)
    flat_bmat = bmat.reshape(-1, n, n)
    flat_inv_bmat = torch.stack([m.inverse() for m in flat_bmat], 0)
    return flat_inv_bmat.view(bmat.shape)


def kl_normal_multivariatenormals(q, p):
    """
    TODO: Please rename to `kl_normal_multivariate_normal`.

    p: (B1, ..., BN, D)
    q: (K1, ..., KN, D)
    output: (B1, ..., BN, K1, ..., KN)
    :param q: Normal posterior distributions (B1, ..., BN, D)
    :param p: multivariate Gaussian prior distributions (K1, ..., KN, D)
    :return: kl between all posteriors in batch and all components (B1, ..., BN, K1, ..., KN)
    """
    batch_shape = q.loc.shape[:-1]
    D = q.loc.shape[-1]
    component_shape = p.loc.shape[:-1]
    assert p.loc.shape[-1] == D

    q_loc = q.loc.contiguous().view(-1, D)
    q_scale = q.scale.contiguous().view(-1, D)
    p_loc = p.loc.contiguous().view(-1, D)
    p_scale_tril = p.scale_tril.contiguous().view(-1, D, D)

    term1 = _batch_diag(p_scale_tril).log().sum(-1)[:, None] - q_scale.log().sum(-1)
    L = _batch_inverse(p_scale_tril)
    term2 = (L.pow(2).sum(-2)[:, None, :] * q_scale.pow(2)).sum(-1)
    term3 = ((p_loc[:, None, :] - q_loc) @ L.transpose(1, 2)).pow(2.0).sum(-1)
    kl = (term1 + 0.5 * (term2 + term3 - D)).transpose(0, 1)
    return kl.view(*batch_shape, *component_shape)