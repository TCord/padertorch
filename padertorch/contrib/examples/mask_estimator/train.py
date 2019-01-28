from pathlib import Path
from warnings import warn

import sacred
from sacred.utils import apply_backspaces_and_linefeeds

from paderbox.database.chime import Chime3
from paderbox.io import dump_json, load_json
from paderbox.utils.nested import deflatten, flatten
from padertorch.configurable import config_to_instance
from padertorch.configurable import recursive_class_to_str
from padertorch.contrib.jensheit.data import MaskProvider
from padertorch.models.mask_estimator import MaskEstimatorModel
from padertorch.train.optimizer import Adam
from padertorch.train.trainer import Trainer

ex = sacred.Experiment('Train Mask Estimator')

ex.captured_out_filter = apply_backspaces_and_linefeeds


@ex.config
def config():
    trainer_opts = {}
    provider_opts = {}
    Trainer.get_config(
        deflatten({
            'model.cls': MaskEstimatorModel,
            'optimizer.cls': Adam,
            'max_trigger': (int(1e5), 'iteration'),
            'summary_trigger': (500, 'iteration'),
            'checkpoint_trigger': (500, 'iteration'),
            'storage_dir': None,
            'keep_all_checkpoints': False,
            'seed': 0
        }),
        trainer_opts,
    )
    MaskProvider.get_config(
        deflatten({
            'database.cls': Chime3
        }),
        provider_opts
    )
    validation_length = 10  # number of examples taken from the validation iterator


def compare_configs(storage_dir, config):
    config = flatten(config)
    init = flatten(load_json(Path(storage_dir) / 'init.json'))
    assert all([key in config for key in init]), \
        (f'Some keys from the init are no longer used:'
         f'{[key for key in init if not key in config]}')
    if not all([init[key] == config[key] for key in init]):
        warn(f'The following keys have changed in comparison to the init:'
             f'{[key for key in init if init[key] != config[key]]}')
    if not all([key in init for key in config]):
        warn(f'The following keys have been added in comparison to the init:'
             f'{[key for key in config if key not in init]}')


@ex.capture
def initialize_trainer_provider(task, trainer_opts, provider_opts, _run):
    assert len(ex.current_run.observers) == 1, (
        'FileObserver` missing. Add a `FileObserver` with `-F foo/bar/`.'
    )
    storage_dir = Path(ex.current_run.observers[0].basedir)
    config = dict()
    config['trainer_opts'] = trainer_opts
    print(trainer_opts.keys())
    config['trainer_opts']['kwargs']['storage_dir'] = storage_dir
    config['provider_opts'] = provider_opts
    if (storage_dir / 'init.json').exists():
        compare_configs(storage_dir, config)
        new = False
    elif task in ['train', 'create_checkpoint']:
        dump_json(recursive_class_to_str(config), storage_dir / 'init.json')
        new = True
    else:
        raise ValueError(task, storage_dir)
    sacred.commands.print_config(_run)

    # we cannot ask if task==resume, since validation is also an allowed task
    assert new ^ (task not in ['train', 'create_checkpoint']), \
        'Train cannot be called on an existing directory. ' \
        'If your want to restart the training use task=restart'
    trainer = Trainer.from_config(config['trainer_opts'])
    assert isinstance(trainer, Trainer)
    return trainer, config_to_instance(config['provider_opts'])


@ex.command
def restart(validation_length):
    trainer, provider = initialize_trainer_provider(task='restart')
    train_iterator = provider.get_train_iterator()
    eval_iterator = provider.get_eval_iterator(
        num_examples=validation_length
    )
    trainer.load_checkpoint()
    trainer.test_run(train_iterator, eval_iterator)
    trainer.train(train_iterator, eval_iterator)


@ex.command
def validate(_config):
    import os
    import torch
    import numpy as np
    from functools import partial
    from paderbox.io import dump_json
    from concurrent.futures import ThreadPoolExecutor

    from padertorch.contrib.jensheit.evaluation import evaluate_masks

    assert len(ex.current_run.observers) == 1, (
        'FileObserver` missing. Add a `FileObserver` with `-F foo/bar/`.'
    )
    storage_dir = Path(ex.current_run.observers[0].basedir)
    assert not (storage_dir / 'results.json').exists(), (
        f'model_dir has already bin evaluatet, {storage_dir}')
    trainer, provider = initialize_trainer_provider(task='validate')
    checkpoint = torch.load(trainer.checkpoint_dir / 'ckpt_best_loss.pth')
    checkpoint = checkpoint['model']
    trainer.model.load_state_dict(checkpoint)
    provider.opts.multichannel = True
    batch_size = 1
    provider.opts.batch_size = batch_size
    eval_iterator = provider.get_eval_iterator()
    evaluation_json = dict(snr=dict(), pesq=dict())
    trainer.model.cpu()
    with ThreadPoolExecutor(os.cpu_count()) as executor:
        for example_id, snr, pesq in executor.map(
                partial(evaluate_masks, model=trainer.model,
                        transform=provider.transformer), eval_iterator):
            evaluation_json['snr'][example_id] = snr
            evaluation_json['pesq'][example_id] = pesq
    evaluation_json['pesq_mean'] = np.mean(
        [value for value in evaluation_json['pesq'].values()])
    evaluation_json['snr'] = np.mean(
        [value for value in evaluation_json['snr'].values()])
    dump_json(evaluation_json, storage_dir / 'results.json')


@ex.command
def create_checkpoint(_config):
    # This may be useful to merge to separatly trained models into one
    raise NotImplementedError


@ex.automain
def train(validation_length):
    trainer, provider = initialize_trainer_provider(task='train')
    train_iterator = provider.get_train_iterator()
    eval_iterator = provider.get_eval_iterator(
        num_examples=validation_length
    )
    trainer.test_run(train_iterator, eval_iterator)
    trainer.train(train_iterator, eval_iterator)