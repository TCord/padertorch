import unittest
import torch
from padertorch.contrib.je.modules.conv import CNN


class TestCNN(unittest.TestCase):
    def test_shapes_1d(self):
        batch_size = 8
        n_frames = 129

        input_size = 40
        latent_dim = 16

        x = torch.ones(batch_size, input_size, n_frames)
        for n_scales in [None, 1, 2]:
            for pooling in ['max', 'avg']:
                for pool_size in [1, 2]:
                    for padding in ['both', None]:
                        enc = CNN.from_config(
                            CNN.get_config(
                                updates=dict(
                                    input_size=input_size, hidden_sizes=256,
                                    num_layers=5, kernel_sizes=3,
                                    output_size=latent_dim,
                                    n_scales=n_scales, norm='batch',
                                    pooling=pooling, pool_sizes=pool_size,
                                    paddings=padding
                                )
                            )
                        )
                        z, pooling_data = enc(x)
                        dec = CNN.from_config(
                            CNN.get_config(
                                updates=dict(
                                    input_size=latent_dim, hidden_sizes=256,
                                    num_layers=5, kernel_sizes=3,
                                    output_size=input_size,
                                    transpose=True,
                                    n_scales=n_scales, norm='batch',
                                    pooling=pooling, pool_sizes=pool_size,
                                    paddings=padding
                                )
                            )
                        )
                        x_hat = dec(z, pooling_data=pooling_data[::-1])
                        self.assertEqual(
                            x_hat.shape, (batch_size, input_size, n_frames))

    def test_shapes_2d(self):
        batch_size = 8
        n_feats = 33
        n_frames = 65

        input_size = 1
        latent_dim = 16

        x = torch.ones(batch_size, input_size, n_feats, n_frames)
        for n_scales in [None, 1, 2]:
            for pooling in ['max', 'avg']:
                for pool_size in [1, 2]:
                    for padding in ['both', None]:
                        enc = CNN.from_config(
                            CNN.get_config(
                                updates=dict(
                                    input_size=input_size, hidden_sizes=128,
                                    num_layers=3, kernel_sizes=3, ndim=2,
                                    output_size=latent_dim,
                                    n_scales=n_scales, norm='batch',
                                    pooling=pooling, pool_sizes=pool_size,
                                    paddings=padding
                                )
                            )
                        )
                        z, pooling_data = enc(x)
                        dec = CNN.from_config(
                            CNN.get_config(
                                updates=dict(
                                    input_size=latent_dim, hidden_sizes=128,
                                    num_layers=3, kernel_sizes=3, ndim=2,
                                    output_size=input_size,
                                    transpose=True,
                                    n_scales=n_scales, norm='batch',
                                    pooling=pooling, pool_sizes=pool_size,
                                    paddings=padding
                                )
                            )
                        )
                        x_hat = dec(z, pooling_data=pooling_data[::-1])
                        self.assertEqual(
                            x_hat.shape, (batch_size, input_size, n_feats, n_frames))