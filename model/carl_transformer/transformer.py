import math
import numpy as np
from copy import deepcopy
import torch
import torch.nn as nn
import torch.nn.functional as F

from carl_transformer.resnet_c2d import *
import sys
from icecream import ic

def attention(Q, K, V, mask=None, dropout=None, visual=False):
    # Q, K, V are (B, *(H), seq_len, d_model//H = d_k)
    # mask is     (B,    1,       1,               Ss)
    d_k = Q.size(-1)
    # (B, H, S, S)
    QKt = Q.matmul(K.transpose(-1, -2))
    sm_input = QKt / np.sqrt(d_k)

    if mask is not None:
        sm_input = sm_input.masked_fill(mask == 0, -float('inf'))

    # try:
    softmax = F.softmax(sm_input, dim=-1)
    # except:
    #     print('softmax failed: ' , sm_input)
    #     raise ValueError(sm_input)


    out = softmax.matmul(V)

    if dropout is not None:
        out = dropout(out)

    # (B, *(H), seq_len, d_model//H = d_k)
    if visual:
        return out, softmax.detach()
    else:
        return out


class MultiheadedAttention(nn.Module):
    def __init__(self, d_model_Q, d_model_K, d_model_V, H, dout_p=0.0, d_model=None, d_out=None, test= False):
        super(MultiheadedAttention, self).__init__()
        self.d_model_Q = d_model_Q
        self.d_model_K = d_model_K
        self.d_model_V = d_model_V
        self.H = H
        self.d_model = d_model
        self.dout_p = dout_p
        self.d_out = d_out
        if self.d_out is None:
            self.d_out = self.d_model_Q

        if self.d_model is None:
            self.d_model = self.d_model_Q

        self.d_k = self.d_model // H

        self.linear_Q2d = nn.Linear(self.d_model_Q, self.d_model)
        self.linear_K2d = nn.Linear(self.d_model_K, self.d_model)
        self.linear_V2d = nn.Linear(self.d_model_V, self.d_model)
        self.linear_d2Q = nn.Linear(self.d_model, self.d_out)

        if test :
            for name,parameter in self.linear_Q2d.named_parameters():
                parameter.data.fill_(.1)
            for name,parameter in self.linear_K2d.named_parameters():
                parameter.data.fill_(.1)
            for name,parameter in self.linear_V2d.named_parameters():
                parameter.data.fill_(.1)
            for name,parameter in self.linear_d2Q.named_parameters():
                parameter.data.fill_(.1)
            self.dout_p = 0

        self.dropout = nn.Dropout(self.dout_p)
        self.visual = False

        assert self.d_model % H == 0

    def forward(self, Q, K, V, mask=None):
        ''' 
            Q, K, V: (B, Sq, Dq), (B, Sk, Dk), (B, Sv, Dv)
            mask: (B, 1, Sk)
            Sk = Sv, 
            Dk != self.d_k
        '''
        B, Sq, d_model_Q = Q.shape
        # (B, Sm, D) <- (B, Sm, Dm)
        Q = self.linear_Q2d(Q)
        K = self.linear_K2d(K)
        V = self.linear_V2d(V)

        # (B, H, Sm, d_k) <- (B, Sm, D)
        Q = Q.view(B, -1, self.H, self.d_k).transpose(-3, -2)  # (-4, -3*, -2*, -1)
        K = K.view(B, -1, self.H, self.d_k).transpose(-3, -2)
        V = V.view(B, -1, self.H, self.d_k).transpose(-3, -2)

        if mask is not None:
            # the same mask for all heads -> (B, 1, 1, Sm2)
            mask = mask.view(B,1,-1)
            mask = mask.unsqueeze(1)

        # (B, H, Sq, d_k) <- (B, H, Sq, d_k), (B, H, Sk, d_k), (B, H, Sv, d_k), Sk = Sv
        if self.visual:
            Q, self.attn_matrix = attention(Q, K, V, mask, self.dropout, self.visual)
            self.attn_matrix = self.attn_matrix.mean(-3)
        else:
            Q = attention(Q, K, V, mask, self.dropout)
        # (B, Sq, D) <- (B, H, Sq, d_k)
        Q = Q.transpose(-3, -2).contiguous().view(B, Sq, self.d_model)
        # (B, Sq, Dq)
        Q = self.linear_d2Q(Q)

        return Q

def clone(module, N):
    return nn.ModuleList([deepcopy(module) for _ in range(N)])

def generate_sincos_embedding(seq_len, d_model, train_len=None):
    odds = np.arange(0, d_model, 2)
    evens = np.arange(1, d_model, 2)
    pos_enc_mat = np.zeros((seq_len, d_model))

    if train_len is None:
        pos_list = np.arange(seq_len)
    else:
        pos_list = np.linspace(0, train_len-1, num=seq_len)

    for i, pos in enumerate(pos_list):
        pos_enc_mat[i, odds] = np.sin(pos / (10000 ** (odds / d_model)))
        pos_enc_mat[i, evens] = np.cos(pos / (10000 ** (evens / d_model)))

    return torch.from_numpy(pos_enc_mat).unsqueeze(0)

class PositionalEncoder(nn.Module):
    def __init__(self, cfg, d_model, dout_p, seq_len=3660,test=False):
        super(PositionalEncoder, self).__init__()
        self.cfg = cfg
        self.d_model = d_model
        if test :
            dout_p = 0
        self.dropout = nn.Dropout(dout_p)
        self.seq_len = seq_len

    def forward(self, x):
        B, S, d_model = x.shape
        if S != self.seq_len:
            pos_enc_mat = generate_sincos_embedding(S, d_model, self.seq_len)
            x = x + pos_enc_mat.type_as(x)
        else:
            pos_enc_mat = generate_sincos_embedding(S, d_model)
            x = x + pos_enc_mat.type_as(x)
        x = self.dropout(x)
        return x

class ResidualConnection(nn.Module):
    def __init__(self, size, dout_p,test=False):
        super(ResidualConnection, self).__init__()
        self.norm = nn.LayerNorm(size)
        if test:
            dout_p = 0
        self.dropout = nn.Dropout(dout_p)

    def forward(self, x, sublayer): 
        # x (B, S, D)
        res = self.norm(x)
        res = sublayer(res)
        res = self.dropout(res)

        return x + res

class BridgeConnection(nn.Module):
    def __init__(self, in_dim, out_dim, dout_p):
        super(BridgeConnection, self).__init__()
        self.norm = nn.LayerNorm(in_dim)
        self.linear = nn.Linear(in_dim, out_dim)
        self.dropout = nn.Dropout(dout_p)
        self.activation = nn.ReLU(inplace=True)

    def forward(self, x):
        x = self.norm(x)
        x = self.linear(x)
        x = self.dropout(x)
        return self.activation(x)


class PositionwiseFeedForward(nn.Module):
    def __init__(self, d_model, d_ff, dout_p, test = False):
        super(PositionwiseFeedForward, self).__init__()
        self.d_model = d_model
        self.d_ff = d_ff
        self.dout_p = dout_p
        self.fc1 = nn.Linear(d_model, d_ff)
        self.fc2 = nn.Linear(d_ff, d_model)
        if test:
            for name,parameter in self.fc1.named_parameters():
                parameter.data.fill_(.1)
            for name,parameter in self.fc2.named_parameters():
                parameter.data.fill_(.1)
            dout_p = 0

        self.dropout = nn.Dropout(dout_p)
        self.activation = nn.ReLU(inplace=True)

    def forward(self, x):
        '''In, Out: (B, S, D)'''
        x = self.fc1(x)
        x = self.activation(x)
        x = self.dropout(x)
        x = self.fc2(x)

        return x

class EncoderLayer(nn.Module):
    
    def __init__(self, d_model, dout_p, H=8, d_ff=None, d_hidden=None , test = False):
        super(EncoderLayer, self).__init__()
        self.res_layer0 = ResidualConnection(d_model, dout_p,test=test)
        self.res_layer1 = ResidualConnection(d_model, dout_p,test=test)
        if d_hidden is None: d_hidden = d_model
        if d_ff is None: d_ff = 4*d_model
        self.self_att = MultiheadedAttention(d_model, d_model, d_model, H, d_model=d_hidden, test=test)
        self.feed_forward = PositionwiseFeedForward(d_model, d_ff, dout_p=0.0,test=test)
        if not test:
            for p in self.parameters():
                if p.dim() > 1:
                    nn.init.xavier_uniform_(p)
        
    def forward(self, x, src_mask=None):
        '''
        in:
            x: (B, S, d_model), src_mask: (B, 1, S)
        out:
            (B, S, d_model)
        '''
        # sublayer should be a function which inputs x and outputs transformation
        # thus, lambda is used instead of just `self.self_att(x, x, x)` which outputs 
        # the output of the self attention
        sublayer0 = lambda x: self.self_att(x, x, x, src_mask)
        sublayer1 = self.feed_forward
        
        x = self.res_layer0(x, sublayer0)
        x = self.res_layer1(x, sublayer1)
        
        return x

class Encoder(nn.Module):
    def __init__(self, d_model, dout_p, H, d_ff, N, d_hidden=None,test=False):
        super(Encoder, self).__init__()
        self.enc_layers = clone(EncoderLayer(d_model, dout_p, H, d_ff, d_hidden,test=test), N)
        
    def forward(self, x, src_mask=None):
        '''
        in:
            x: (B, S, d_model) src_mask: (B, 1, S)
        out:
            # x: (B, S, d_model) which will be used as Q and K in decoder
        '''
        for layer in self.enc_layers:
            x = layer(x, src_mask)
        return x


class TransformerEmbModel(nn.Module):
    def __init__(self, cfg,test = False):
        super().__init__()
        self.cfg = cfg
        drop_rate = cfg.MODEL.EMBEDDER_MODEL.FC_DROPOUT_RATE
        in_channels = cfg.MODEL.BASE_MODEL.OUT_CHANNEL
        cap_scalar = cfg.MODEL.EMBEDDER_MODEL.CAPACITY_SCALAR
        fc_params = cfg.MODEL.EMBEDDER_MODEL.FC_LAYERS
        self.embedding_size = cfg.MODEL.EMBEDDER_MODEL.EMBEDDING_SIZE
        hidden_channels = cfg.MODEL.EMBEDDER_MODEL.HIDDEN_SIZE
        self.pooling = nn.AdaptiveMaxPool2d(1)
        
        self.fc_layers = []

        if test:
            drop_rate = 0

        for channels, activate in fc_params:
            channels = channels*cap_scalar
            self.fc_layers.append(nn.Dropout(drop_rate))
            self.fc_layers.append(nn.Linear(in_channels, channels))
            self.fc_layers.append(nn.BatchNorm1d(channels))
            self.fc_layers.append(nn.ReLU(True))
            in_channels = channels
        
        self.fc_layers = nn.Sequential(*self.fc_layers)

        
        self.video_emb = nn.Linear(in_channels, hidden_channels)
        

        self.video_pos_enc = PositionalEncoder(cfg, hidden_channels, drop_rate, seq_len=cfg.TRAIN.NUM_FRAMES,test=test)
        if cfg.MODEL.EMBEDDER_MODEL.NUM_LAYERS > 0:
            self.video_encoder = Encoder(hidden_channels, drop_rate, cfg.MODEL.EMBEDDER_MODEL.NUM_HEADS, 
                                            cfg.MODEL.EMBEDDER_MODEL.D_FF, cfg.MODEL.EMBEDDER_MODEL.NUM_LAYERS,test=test)
        
        self.embedding_layer = nn.Linear(hidden_channels, self.embedding_size)

        if test:
            for name,paramter in self.fc_layers.named_parameters():
                paramter.data.fill_(.1)
            for name,paramter in self.video_emb.named_parameters():
                paramter.data.fill_(.1)
            for name,paramter in self.embedding_layer.named_parameters():
                paramter.data.fill_(.1)

    def forward(self, x, video_maskss=None):
        batch_size, num_steps, c, h, w = x.shape
        x_res = x
        x = x.view(batch_size*num_steps, c, h, w)
        x_reshape = x
        x = self.pooling(x)
        x_pooling = x
        x = torch.flatten(x, start_dim=1)
        x_flatten = x

        x = self.fc_layers(x)
        x_fc = x
        x = self.video_emb(x)
        x_emb =x
        x = x.view(batch_size, num_steps, x.size(1))

        x_transform = x
        x = self.video_pos_enc(x)
        x_pos = x
        if self.cfg.MODEL.EMBEDDER_MODEL.NUM_LAYERS > 0:
            x = self.video_encoder(x, src_mask=video_maskss)
        x_encoding_layer = x
        

        x = x.view(batch_size*num_steps, -1)
        x = self.embedding_layer(x)
        x = x.view(batch_size, num_steps, self.embedding_size)
        return x_res,x_reshape,x_pooling,x_flatten,x_fc,x_emb,x_transform,x_pos,x_encoding_layer,x

class TransformerModel(nn.Module):
    def __init__(self, cfg,test=False):
        super().__init__()
        self.cfg = cfg
        self.test = test
        res50_model = models.resnet50(pretrained=True)
        if cfg.MODEL.BASE_MODEL.LAYER == 3:
            self.backbone = nn.Sequential(*list(res50_model.children())[:-3]) # output of layer "4" (counting starts from 0): 1024x14x14
            self.res_finetune = list(res50_model.children())[-3]
            cfg.MODEL.BASE_MODEL.OUT_CHANNEL = 2048
        elif cfg.MODEL.BASE_MODEL.LAYER == 2:
            self.backbone = nn.Sequential(*list(res50_model.children())[:-4]) # output of layer 3
            self.res_finetune = nn.Sequential(*list(res50_model.children())[-4:-2])
            cfg.MODEL.BASE_MODEL.OUT_CHANNEL = 2048
        else:
            self.backbone = nn.Sequential(*list(res50_model.children())[:-2]) # output of layer 5 : 2048x7x7
            self.res_finetune = nn.Identity()
            cfg.MODEL.BASE_MODEL.OUT_CHANNEL = 2048
        self.embed = TransformerEmbModel(cfg,test=test)
        self.embedding_size = self.embed.embedding_size
        
        if cfg.MODEL.PROJECTION:
            self.ssl_projection = MLPHead(cfg,test=test)
        if cfg.TRAINING_ALGO == 'classification':
            self.classifier = Classifier(cfg)
            

        ## validation 
        if self.test:
            from ..transformer.transformer import CARL
            self.mine = CARL(cfg,test=True)

    def forward(self, x, num_frames=None, video_masks=None,skeleton=None, classification=False,split="train"):
        torch.set_printoptions(sci_mode=False)
        np.set_printoptions(suppress=True)

        ori_x = x

        batch_size, num_steps, c, h, w = x.shape
        frames_per_batch = self.cfg.MODEL.BASE_MODEL.FRAMES_PER_BATCH
        num_blocks = int(math.ceil(float(num_steps)/frames_per_batch))
        backbone_out = []
        for i in range(num_blocks):
            curr_idx = i * frames_per_batch
            cur_steps = min(num_steps-curr_idx, frames_per_batch) ## make sure the next line will not be out of bound
            curr_data = x[:, curr_idx:curr_idx+cur_steps]         ## take a batch for resnet to encode (size will be BASRMODEL.BATCH_SIZE or the remainder)
            curr_data = curr_data.contiguous().view(-1, c, h, w)
            self.backbone.eval()
            with torch.no_grad():
                curr_emb = self.backbone(curr_data)
            curr_emb = self.res_finetune(curr_emb)
            _, out_c, out_h, out_w = curr_emb.size()
            curr_emb = curr_emb.contiguous().view(batch_size, cur_steps, out_c, out_h, out_w)
            backbone_out.append(curr_emb)
        x = torch.cat(backbone_out, dim=1)
        x_res,x_reshape,x_pooling,x_flatten,x_fc,x_emb,x_transform,x_pos,x_encoding_layer,x_encoder = self.embed(x, video_maskss=video_masks)
        if self.test:
            mine_res = self.mine.resnet50(ori_x)
            mine_transformation = self.mine.transformation(mine_res,batch_size,num_steps)
            mine_encoder = self.mine.encoder(mine_transformation,video_masks)


        if self.cfg.MODEL.PROJECTION and split=="train":
            x1,x2,x = self.ssl_projection(x_encoder)
            x = F.normalize(x, dim=-1)
        elif self.cfg.MODEL.L2_NORMALIZE:
            x = F.normalize(x_encoder, dim=-1)
        if classification:
            return self.classifier(x)
        
        if self.test:
            ic(torch.equal(x_flatten,mine_res))
            ic(torch.equal(x_transform,mine_transformation) )
            ic(torch.equal(x_encoder,mine_encoder))
            mine_encoder_result = mine_encoder.reshape(-1,128)
            mine_encoder_result = self.mine.projection(mine_encoder_result)
            mine_encoder_result = mine_encoder_result.view(batch_size,num_steps,-1)
            mine_encoder_result = F.normalize(mine_encoder_result, dim=-1)
            ic(torch.equal(x,mine_encoder_result),x.shape,mine_encoder_result.shape)
            mine_result = self.mine(ori_x,split=split)
            ic(torch.equal(mine_encoder_result,mine_result))
            ic(x,mine_result)
        return x
        return {"x_res":x_res,
                "x_reshape":x_reshape,
                "x_pooling":x_pooling,
                "x_flatten":x_flatten,
                "x_fc":x_fc,
                "x_emb":x_emb,
                "x_transform":x_transform,
                "x_pos":x_pos,
                "x_encoding_layer":x_encoding_layer,
                "x":x}

