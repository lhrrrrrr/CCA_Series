import torch
import torch.nn as nn
import torch.nn.functional as F
import numpy as np
from linear_cca import linear_cca
from torch.utils.data import BatchSampler, SequentialSampler, RandomSampler
from models import DCCAE
from utils import load_data
import time
import logging
try:
    import cPickle as thepickle
except ImportError:
    import _pickle as thepickle

import numpy as np
import torch.nn as nn
torch.set_default_tensor_type(torch.DoubleTensor)


class Solver():
    def __init__(self, model, linear_cca, outdim_size, epoch_num, batch_size, learning_rate, reg_par, device=torch.device('cpu')):
        self.model = nn.DataParallel(model)
        self.model.to(device)
        self.epoch_num = epoch_num
        self.batch_size = batch_size
        self.loss = model.loss
        self.optimizer = torch.optim.RMSprop(
            self.model.parameters(), lr=learning_rate, weight_decay=reg_par)
        self.device = device

        self.linear_cca = linear_cca

        self.outdim_size = outdim_size

        formatter = logging.Formatter(
            "[ %(levelname)s : %(asctime)s ] - %(message)s")
        logging.basicConfig(
            level=logging.DEBUG, format="[ %(levelname)s : %(asctime)s ] - %(message)s")
        self.logger = logging.getLogger("Pytorch")
        fh = logging.FileHandler("Dccae.log")
        fh.setFormatter(formatter)
        self.logger.addHandler(fh)

        self.logger.info(self.model)
        self.logger.info(self.optimizer)

    def fit(self, x1, x2, vx1=None, vx2=None, tx1=None, tx2=None, checkpoint='DCCAE_checkpoint.model'):
        """

        x1, x2 are the vectors needs to be make correlated
        dim=[batch_size, feats]

        """
        x1 = x1.to(self.device)
        x2 = x2.to(self.device)

        #if checkpoint is not None:
        #   checkpoint_ = torch.load(checkpoint)
        #   solver.model.load_state_dict(checkpoint_)
        #  print("加载模型{}进行训练".format(checkpoint))

        data_size = x1.size(0)

        best_val_loss = 0
        train_losses = []

        for epoch in range(self.epoch_num):
            epoch_start_time = time.time()
            self.model.train()
            batch_idxs = list(BatchSampler(RandomSampler(
                range(data_size)), batch_size=self.batch_size, drop_last=False))
            for batch_idx in batch_idxs:
                self.optimizer.zero_grad()
                batch_x1 = x1[batch_idx, :]
                batch_x2 = x2[batch_idx, :]
                o1, o2, re1, re2 = self.model(batch_x1, batch_x2)
                loss = self.loss(o1, o2, batch_x1, batch_x2, re1, re2)
                train_losses.append(loss.item())
                loss.backward()
                self.optimizer.step()
            train_loss = np.mean(train_losses)

            info_string = "Epoch {:d}/{:d} - time: {:.2f} - training_loss: {:.4f}"
            if vx1 is not None and vx2 is not None:
                with torch.no_grad():
                    self.model.eval()
                    val_loss = self.test(vx1, vx2)
                    info_string += " - val_loss: {:.4f}".format(val_loss)
                    if val_loss < best_val_loss:
                        self.logger.info(
                            "Epoch {:d}: val_loss improved from {:.4f} to {:.4f}, saving model to {}".format(
                                epoch + 1, best_val_loss, val_loss, checkpoint))
                        best_val_loss = val_loss
                        # 保存当前val_loss最低的模型参数
                        torch.save(self.model.state_dict(), checkpoint)
                    else:
                        self.logger.info("Epoch {:d}: val_loss did not improve from {:.4f}".format(
                            epoch + 1, best_val_loss))
            else:
                torch.save(self.model.state_dict(), checkpoint)
            epoch_time = time.time() - epoch_start_time
            self.logger.info(info_string.format(
                epoch + 1, self.epoch_num, epoch_time, train_loss))

        checkpoint_ = torch.load(checkpoint)
        self.model.load_state_dict(checkpoint_)
        if vx1 is not None and vx2 is not None:
            loss = self.test(vx1, vx2)
            self.logger.info("loss on validation data: {:.4f}".format(loss))

        if tx1 is not None and tx2 is not None:
            loss = self.test(tx1, tx2)
            self.logger.info('loss on test data: {:.4f}'.format(loss))



    def test(self, x1, x2, use_linear_cca=False):
        x1 = x1.to(self.device)
        x2 = x2.to(self.device)
        with torch.no_grad():
            losses, outputs = self._get_outputs(x1, x2)

            if use_linear_cca:
                print("Linear CCA started!")
                outputs = self.linear_cca.test(outputs[0], outputs[1])
                return np.mean(losses), outputs
            else:
                return np.mean(losses)

    # 通过训练数据初始化linear_cca
    def init_linear_cca(self, x1, x2):
        x1 = x1.to(self.device)
        x2 = x2.to(self.device)
        # train_linear_cca
        if self.linear_cca is not None:
            _, outputs = self._get_outputs(x1, x2)
            self.train_linear_cca(outputs[0], outputs[1])

    def train_linear_cca(self, x1, x2):
        self.linear_cca.fit(x1, x2, self.outdim_size)

    def _get_outputs(self, x1, x2):
        with torch.no_grad():
            self.model.eval()
            data_size = x1.size(0)
            batch_idxs = list(BatchSampler(SequentialSampler(range(data_size)), batch_size=self.batch_size, drop_last=False))
            losses = []
            outputs1 = []
            outputs2 = []
            for batch_idx in batch_idxs:
                batch_x1 = x1[batch_idx, :]
                batch_x2 = x2[batch_idx, :]
                o1, o2, re1 ,re2 = self.model(batch_x1, batch_x2)
                outputs1.append(o1)
                outputs2.append(o2)
                loss = self.loss(o1, o2, batch_x1, batch_x2, re1, re2)
                losses.append(loss.item())
        outputs = [torch.cat(outputs1, dim=0).cpu().numpy(),
                   torch.cat(outputs2, dim=0).cpu().numpy()]
        return losses, outputs


if __name__ == '__main__':
    ############
    # Parameters Section

    device = torch.device('cuda')
    # device = torch.device('cuda:0')
    print(device)
    print("Using", torch.cuda.device_count(), "GPUs")


    # the size of the new space learned by the model (number of the new features)
    outdim_size = 10

    # size of the input for view 1 and view 2
    input_shape1 = 784
    input_shape2 = 784

    # number of layers with nodes in each one
    layer_sizes1 = [1024, 1024, 1024, outdim_size]
    layer_sizes2 = [1024, 1024, 1024, outdim_size]

    # the parameters for training the network
    learning_rate = 1e-3
    epoch_num = 20
    batch_size = 800

    # the regularization parameter of the network
    # seems necessary to avoid the gradient exploding especially when non-saturating activations are used
    reg_par = 1e-5

    # specifies if all the singular values should get used to calculate the correlation or just the top outdim_size ones
    # if one option does not work for a network or dataset, try the other one
    use_all_singular_values = False

    # if a linear CCA should get applied on the learned features extracted from the networks
    # it does not affect the performance on noisy MNIST significantly
    apply_linear_cca = True
    # end of parameters section
    ############

    # Each view is stored in a gzip file separately. They will get downloaded the first time the code gets executed.
    # Datasets get stored under the datasets folder of user's Keras folder
    # normally under [Home Folder]/.keras/datasets/
    data1 = load_data('../dataset/noisymnist_view1.gz')
    data2 = load_data('../dataset/noisymnist_view2.gz')
    # Building, training, and producing the new features by Dccae
    model = DCCAE(layer_sizes1, layer_sizes2, input_shape1,
                    input_shape2, outdim_size, use_all_singular_values, device=device).double()
    l_cca = None
    if apply_linear_cca:
        l_cca = linear_cca()
    solver = Solver(model, l_cca, outdim_size, epoch_num, batch_size,
                    learning_rate, reg_par, device=device)
    train1, train2 = data1[0][0], data2[0][0]
    val1, val2 = data1[1][0], data2[1][0]
    test1, test2 = data1[2][0], data2[2][0]

    solver.fit(train1, train2, val1, val2, test1, test2)
    # TODO: Save l_cca model if needed

    # d = torch.load('DCCAE_checkpoint.model')
    # solver.model.load_state_dict(d)
    # print(solver.model.parameters())
