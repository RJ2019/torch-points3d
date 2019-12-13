from abc import ABC, abstractmethod
import math
import torch
from torch.nn import Sequential as Seq, Linear as Lin, ReLU, LeakyReLU, BatchNorm1d as BN, Dropout
from torch_geometric.nn import knn_interpolate, fps, radius, global_max_pool, global_mean_pool, knn


def MLP(channels, activation=ReLU()):
    return Seq(*[
        Seq(Lin(channels[i - 1], channels[i]), activation, BN(channels[i]))
        for i in range(1, len(channels))
    ])

class FPModule(torch.nn.Module):
    """ Upsampling module from PointNet++

    Arguments:
        k [int] -- number of nearest neighboors used for the interpolation
        up_conv_nn [List[int]] -- list of feature sizes for the uplconv mlp

    Returns:
        [type] -- [description]
    """

    def __init__(self, up_k, up_conv_nn, *args, **kwargs):
        super(FPModule, self).__init__()
        self.k = up_k
        self.nn = MLP(up_conv_nn)

    def forward(self, data):
        #print([x.shape if x is not None else x for x in data])
        x, pos, batch, x_skip, pos_skip, batch_skip = data
        x = knn_interpolate(x, pos, pos_skip, batch, batch_skip, k=self.k)
        if x_skip is not None:
            x = torch.cat([x, x_skip], dim=1)
        x = self.nn(x)
        data = (x, pos_skip, batch_skip)
        return data
        
class BaseConvolution(ABC, torch.nn.Module):
    def __init__(self, ratio, radius, *args, **kwargs):
        torch.nn.Module.__init__(self)

        self.ratio = ratio
        self.radius = radius
        self.max_num_neighbors = kwargs.get("max_num_neighbors", 64)

    @property
    @abstractmethod
    def conv(self):
        pass

    def forward(self, data):
        x, pos, batch = data
        idx = fps(pos, batch, ratio=self.ratio)
        row, col = radius(pos, pos[idx], self.radius, batch, batch[idx],
                          max_num_neighbors=self.max_num_neighbors)
        edge_index = torch.stack([col, row], dim=0)
        x = self.conv(x, (pos, pos[idx]), edge_index)
        pos, batch = pos[idx], batch[idx]
        data = (x, pos, batch)
        return data

class BaseResnetBlock(ABC, torch.nn.Module):

    def __init__(self, indim, outdim, convdim):
        '''
            indim: size of x at the input
            outdim: desired size of x at the output
            convdim: size of x following convolution
        '''
        torch.nn.Module.__init__(self)

        self.indim = indim
        self.outdim = outdim
        self.convdim = convdim

        self.features_downsample_nn = MLP([self.indim, self.outdim//4])
        self.features_upsample_nn = MLP([self.convdim, self.outdim])

        self.shortcut_feature_resize_nn = MLP([self.indim, self.outdim])

        self.activation = ReLU()

    @abstractmethod
    def convolution(self, data):
        pass

    def forward(self, data):

        x, pos, batch = data #(N, indim)

        shortcut = x #(N, indim)

        x = self.features_downsample_nn(x) #(N, outdim//4)

        #if this is an identity resnet block, idx will be None
        x, pos, batch, idx = self.convolution((x, pos, batch)) #(N', convdim)

        x = self.features_upsample_nn(x) #(N', outdim)

        if idx is not None:
            shortcut = shortcut[idx] #(N', indim)

        shortcut = self.shortcut_feature_resize_nn(shortcut) #(N', outdim)

        x = shortcut + x

        return self.activation(x), pos, batch

class GlobalBaseModule(torch.nn.Module):
    def __init__(self, nn, aggr='max'):
        super(GlobalBaseModule, self).__init__()
        self.nn = MLP(nn)
        self.pool = global_max_pool if aggr == "max" else global_mean_pool

    def forward(self, data):
        x, pos, batch = data
        x = self.nn(torch.cat([x, pos], dim=1))
        x = self.pool(x, batch)
        pos = pos.new_zeros((x.size(0), 3))
        batch = torch.arange(x.size(0), device=batch.device)
        data = (x, pos, batch)
        return data