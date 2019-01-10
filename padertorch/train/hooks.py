from collections import defaultdict

import sys
import numpy as np
import torch
from cached_property import cached_property
from tensorboardX import SummaryWriter

import datetime
import padertorch as pt
from padertorch.train.trigger import IntervalTrigger, EndTrigger, OrTrigger

__all__ = [
    'SummaryHook',
    'ValidationHook',
    # 'ProgressBarHook',
    'StopTrainingHook',
    'StopTraining'
]


class BaseHook:
    def __init__(self, trigger=None):
        """
        :param trigger: Length of step between occurences or Trigger.
            It consists of an integer and either 'epoch' or 'iteration'
        """
        if trigger is not None:
            self.trigger = IntervalTrigger.new(trigger)

    @property
    def priority(self):
        """
        Summary 50
        Print 40 NotImplemented
        ProgressBar(TQDM) 30 NotImplemented
        Validation / Checkpoint 20
        End 10

        End has to be the last one
        Summary before Validation, clears timer information
        Print and ProgressBar may access Summary
        """
        return 15

    def pre_step(self, trainer: 'pt.Trainer'):
        """
        function is called before each iteration of the train iterator
        :param trainer:
        :return:
        """
        pass

    def post_step(self, trainer: 'pt.Trainer', example, model_output,
                      review):
        """
        function is called after each train step
        :param trainer:
        :param example:
        :param model_output:
        :param review:
        :return:
        """
        pass

    def close(self, trainer: 'pt.Trainer'):
        pass


class SummaryHook(BaseHook):
    def __init__(self, trigger, validate=None,
                 summary_prefix='training'):
        super().__init__()

        if validate is None:
            super().__init__(trigger)
        else:
            super().__init__(OrTrigger(
                IntervalTrigger.new(trigger),
                IntervalTrigger.new(validate),
            ))
        self.reset_summary()
        self.summary_prefix = summary_prefix
        self.storage_dir = None

    @property
    def priority(self):
        return 50

    @cached_property
    def writer(self):
        return SummaryWriter(str(self.storage_dir),
                             filename_suffix=self.summary_prefix)

    @staticmethod
    def empty_summary_dict():
        return dict(
            losses=defaultdict(list),
            scalars=defaultdict(list),
            histograms=defaultdict(list),
            audios=dict(),
            images=dict()
        )

    def reset_summary(self):
        # Todo: add figures
        self.summary = self.empty_summary_dict()

    def update_summary(self, review):
        for key, loss in review.get('losses', dict()).items():
            self.summary['losses'][key].append(loss.item())
        for key, scalar in review.get('scalars', dict()).items():
            self.summary['scalars'][key].append(
                scalar.item() if torch.is_tensor(scalar) else scalar)
        for key, histogram in review.get('histograms', dict()).items():
            self.summary['histograms'][key] = np.concatenate(
                [self.summary['histograms'].get(key, np.zeros(0)),
                 histogram.clone().cpu().data.numpy().flatten()]
            )[-10000:]  # do not hold more than 10K values in memory
        for key, audio in review.get('audios', dict()).items():
            self.summary['audios'][key] = audio  # snapshot
        for key, image in review.get('images', dict()).items():
            self.summary['images'][key] = image  # snapshot

    def dump_summary(self, trainer):
        iteration = trainer.iteration
        timer = trainer.timer
        prefix = self.summary_prefix
        for key, loss in self.summary['losses'].items():
            self.writer.add_scalar(
                f'{prefix}/{key}', np.mean(loss), iteration)
        for key, scalar in self.summary['scalars'].items():
            self.writer.add_scalar(
                f'{prefix}/{key}', np.mean(scalar), iteration)
        for key, scalar in timer.as_dict.items():
            if key in ['time_per_data_loading', 'time_per_train_step']:
                if 'time_per_step' in timer.as_dict.keys():
                    time_per_step = timer.as_dict['time_per_step']
                    if len(time_per_step) != len(scalar):
                        print(
                            'Warning: padertorch.Trainer timing bug.'
                            f'len(time_per_step) == {len(time_per_step)} '
                            f'!= len(scalar) == {len(scalar)}'
                        )
                    scalar = (
                        scalar.sum() / time_per_step.sum()
                    )
                    if key == 'time_per_data_loading':
                        key = 'time_rel_data_loading'
                    elif key == 'time_per_train_step':
                        key = 'time_rel_train_step'
                else:
                    # Something went wrong, most likely an exception.
                    pass
            self.writer.add_scalar(
                f'{prefix}/{key}', scalar.mean(), iteration)
        for key, histogram in self.summary['histograms'].items():
            self.writer.add_histogram(
                f'{prefix}/{key}', np.array(histogram), iteration
            )
        for key, audio in self.summary['audios'].items():
            if isinstance(audio, (tuple, list)):
                assert len(audio) == 2, (len(audio), audio)
                self.writer.add_audio(
                    f'{prefix}/{key}', audio[0],
                    iteration, sample_rate=audio[1]
                )
            else:
                self.writer.add_audio(
                    f'{prefix}/{key}', audio,
                    iteration, sample_rate=16000
                )
        for key, image in self.summary['images'].items():
            self.writer.add_image(f'{prefix}/{key}', image, iteration)
        self.reset_summary()
        trainer.reset_timer()

    def pre_step(self, trainer: 'pt.Trainer'):
        if (
                self.trigger(iteration=trainer.iteration, epoch=trainer.epoch)
                or trainer.iteration == 1
        ):
            self.dump_summary(trainer)

    def post_step(self, trainer: 'pt.Trainer', example, model_out, review):
        if self.storage_dir is None:
            self.storage_dir = trainer.storage_dir
        else:
            assert self.storage_dir == trainer.storage_dir
        self.update_summary(review)

    def close(self, trainer: 'pt.Trainer'):
        self.dump_summary(trainer)


class ValidationHook(SummaryHook):
    def __init__(self, trigger, iterator):
        super().__init__(trigger, summary_prefix='validation')
        self.iterator = iterator

    @property
    def priority(self):
        return 20

    def pre_step(self, trainer: 'pt.Trainer'):
        if self.trigger(iteration=trainer.iteration, epoch=trainer.epoch):
            assert len(trainer.timer.timings) == 0, trainer.timer
            print('Starting Validation')
            evaluation = trainer.validate(self.iterator)
            [self.update_summary(review) for review in evaluation]
            self.dump_summary(trainer)
            assert len(trainer.timer.timings) == 0, trainer.timer
            print('Finished Validation')

# class ProgressBarHook(BaseHook):
#     def __init__(self, max_step, max_iteration=None,
#                  update_intervall=10, bar_length=100, disable=False):
#         """
#         :param max_step:
#         :param max_iteration: has to be defined if max_steps unit is session
#             integer with the length of the iterator
#         :param update_interval (int): Number of iterations to skip printing the
#             progress bar.
#         :param bar_length (int): Length of the progress bar in characters.
#         :param disable: bool use to disable the entire progressbar wrapper
#         """
#         from tqdm import tqdm
#         super().__init__((update_intervall, 'iteration'))
#         self.update_intervall = update_intervall
#         self.end_trigger = EndTrigger.new(max_step)
#         length, unit = max_step
#         self.unit = unit
#         if unit == 'epoch':
#             self.ep_pbar = tqdm(total=max_step[0], ncols=bar_length,
#                                 disable=disable)
#             if max_iteration is not None:
#                 self.it_pbar = tqdm(total=max_iteration, ncols=bar_length)
#             self.ep_trigger = IntervalTrigger(1, 'epoch')
#         elif unit == 'iteration':
#             self.it_pbar = tqdm(total=max_step[0], ncols=bar_length)
#         else:
#             raise ValueError(f'Unknown unit {unit}')
#
#     def update_timer(self, iteration, epoch):
#         self.end_trigger.set_last(iteration, epoch)
#         self.it_pbar.pos = iteration
#         if self.unit == 'epoch':
#             self.ep_pbar.pos = epoch
#             self.ep_trigger.set_last(iteration, epoch)
#
#     def post_step(self, trainer: 'pt.Trainer', example,
#                       model_output, review):
#         iteration = trainer.iteration
#         epoch = trainer.epoch
#         if self.trigger(iteration, epoch):
#             self.it_pbar.update(self.update_intervall)
#             if self.unit == 'session' and self.ep_trigger(iteration, epoch):
#                 self.ep_pbar.update()
#         if self.end_trigger(iteration, epoch):
#             self.it_pbar.close()
#             self.ep_pbar.close()
#
#
#     @property
#     def priority(self):
#         return 13


class StopTrainingHook(BaseHook):
    def __init__(self, trigger):
        super().__init__()
        self.trigger = EndTrigger.new(trigger)

    @property
    def priority(self):
        return 10

    def pre_step(self, trainer):
        if self.trigger(trainer.iteration, trainer.epoch):
            print(f'Training ended after {trainer.epoch} epochs and'
                  f' {trainer.iteration} iterations')
            raise StopTraining


class StopTraining(Exception):
    pass