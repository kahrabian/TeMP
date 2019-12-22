from models.RGCN import RGCN
import dgl
import numpy as np
from utils.utils import comp_deg_norm, move_dgl_to_cuda
from utils.scores import *
from baselines.TKG_Non_Recurrent import TKG_Non_Recurrent


class StaticRGCN(TKG_Non_Recurrent):
    def __init__(self, args, num_ents, num_rels, graph_dict_train, graph_dict_val, graph_dict_test):
        super(StaticRGCN, self).__init__(args, num_ents, num_rels, graph_dict_train, graph_dict_val, graph_dict_test)

    def build_model(self):
        self.train_seq_len = self.args.train_seq_len
        self.test_seq_len = self.args.test_seq_len
        self.num_pos_facts = self.args.num_pos_facts
        self.ent_encoder = RGCN(self.args, self.hidden_size, self.embed_size, self.num_rels, static=True)

    def evaluate(self, t_list, val=True):
        graph_dict = self.graph_dict_val if val else self.graph_dict_test
        graph_train_list = [self.graph_dict_train[i.item()] for i in t_list]
        g_list = [graph_dict[i.item()] for i in t_list]
        per_graph_ent_embeds = self.get_per_graph_ent_embeds(t_list, graph_train_list, val=True)
        triplets, labels = self.corrupter.sample_labels_val(g_list)
        return self.calc_metrics(per_graph_ent_embeds, t_list, triplets, labels)

    def forward(self, t_list, reverse=False):
        kld_loss = 0
        reconstruct_loss = 0
        g_list = [self.graph_dict_train[i.item()] for i in t_list]

        per_graph_ent_embeds = self.get_per_graph_ent_embeds(t_list, g_list)
        triplets, neg_tail_samples, neg_head_samples, labels = self.corrupter.samples_labels_train(t_list, g_list)

        for i, ent_embed in enumerate(per_graph_ent_embeds):
            loss_tail = self.train_link_prediction(ent_embed, triplets[i], neg_tail_samples[i], labels[i], corrupt_tail=True)
            loss_head = self.train_link_prediction(ent_embed, triplets[i], neg_head_samples[i], labels[i], corrupt_tail=False)
            reconstruct_loss += loss_tail + loss_head
        return reconstruct_loss, kld_loss

    def get_per_graph_ent_embeds(self, t_list, graph_train_list, val=False):
        if val:
            sampled_graph_list = graph_train_list
        else:
            sampled_graph_list = []
            for g in graph_train_list:
                src, rel, dst = g.edges()[0], g.edata['type_s'], g.edges()[1]
                half_num_nodes = int(src.shape[0] / 2)
                graph_split_ids = np.random.choice(np.arange(half_num_nodes),
                                                   size=int(0.5 * half_num_nodes), replace=False)
                graph_split_rev_ids = graph_split_ids + half_num_nodes

                sg = g.edge_subgraph(np.concatenate((graph_split_ids, graph_split_rev_ids)), preserve_nodes=True)
                norm = comp_deg_norm(sg)
                sg.ndata.update({'id': g.ndata['id'], 'norm': torch.from_numpy(norm).view(-1, 1)})
                sg.edata['type_s'] = rel[np.concatenate((graph_split_ids, graph_split_rev_ids))]
                sg.ids = g.ids
                sampled_graph_list.append(sg)
        batched_graph = dgl.batch(sampled_graph_list)
        batched_graph.ndata['h'] = self.ent_embeds[batched_graph.ndata['id']].view(-1, self.embed_size)
        if self.use_cuda:
            move_dgl_to_cuda(batched_graph)
        node_sizes = [len(g.nodes()) for g in graph_train_list]
        enc_ent_mean_graph = self.ent_encoder(batched_graph, reverse=False)
        ent_enc_embeds = enc_ent_mean_graph.ndata['h']
        per_graph_ent_embeds = ent_enc_embeds.split(node_sizes)
        return per_graph_ent_embeds

