"""Rebuild portable UI workflows and equivalent ComfyUI API prompts."""
import copy
import json
from harness import load, ROOT

promax, _ = load()

def build(turbo=False):
    nodes=[]; links=[]; api={}
    def add(id, kind, title, pos, inputs, outputs, values=None, api_values=None):
        n={'id':id,'type':kind,'title':title,'pos':pos,'size':[320,200], 'flags':{},'order':len(nodes),'mode':0,
           'inputs':[{'name':name,'type':typ,'link':None} for name,typ in inputs],
           'outputs':[{'name':name,'type':typ,'links':[],'slot_index':i} for i,(name,typ) in enumerate(outputs)],
           'properties':{'Node name for S&R':kind},'widgets_values':values or []}
        nodes.append(n); api[str(id)]={'class_type':kind,'inputs':api_values or {}}
        return n
    def connect(source, output, dest, input_name):
        a=next(n for n in nodes if n['id']==source);b=next(n for n in nodes if n['id']==dest)
        slot=next(i for i,x in enumerate(b['inputs']) if x['name']==input_name)
        lid=len(links)+1; typ=a['outputs'][output]['type']
        assert b['inputs'][slot]['type']==typ
        links.append([lid,source,output,dest,slot,typ]);a['outputs'][output]['links'].append(lid);b['inputs'][slot]['link']=lid
        api[str(dest)]['inputs'][input_name]=[str(source),output]
    for id,mode in [(1,'fl2va'),(2,'ref2va')]:
        filename=f'minimax_h3_{mode}_pruned_fp8_scaled.safetensors'
        add(id,'UNETLoader',f'{mode.upper()} · selected lazily',[0,40+(id-1)*260],[],[('MODEL','MODEL')],[filename,'default'],{'unet_name':filename,'weight_dtype':'default'})
    clipfile='qwen3vl_4b_fp8_scaled.safetensors' if turbo else 'qwen3vl_32b_minimax_h3_int8_convrot.safetensors'
    add(3,'CLIPLoader','Qwen H3 text encoder',[0,580],[],[('CLIP','CLIP')],[clipfile,'minimax','default'],{'clip_name':clipfile,'type':'minimax','device':'default'})
    for id,filename,y in [(4,'minimax_h3_video_vae_fp8mix.safetensors',850),(5,'minimax_h3_audio_vae_fp32.safetensors',1090)]:
        add(id,'VAELoader','Video VAE' if id==4 else 'Audio VAE',[0,y],[],[('VAE','VAE')],[filename],{'vae_name':filename})
    schema=promax.MiniMaxH3Promax.define_schema()
    fields=[f for f in schema.inputs if 'default' in f.kw]
    params={f.id:f.kw['default'] for f in fields}
    types={'model_fl2va':'MODEL','model_ref2va':'MODEL','clip':'CLIP','vae':'VAE','first_frame':'IMAGE','last_frame':'IMAGE',
           'reference_1':'IMAGE','reference_2':'IMAGE','reference_3':'IMAGE','reference_video':'IMAGE','reference_audio':'AUDIO','audio_vae':'VAE'}
    director=add(6,'MiniMaxH3Promax','MiniMax H3 Promax · Director',[750,40],list(types.items()),
          [('model','MODEL'),('positive','CONDITIONING'),('latent','LATENT'),('fps','FLOAT'),('prompt','STRING'),('report','STRING')],
          list(params.values()),params.copy())
    director['size']=[680,1080]
    add(7,'RandomNoise','Seed',[1550,40],[],[('NOISE','NOISE')],[42,'fixed'],{'noise_seed':42})
    add(8,'KSamplerSelect','Sampler',[1550,270],[],[('SAMPLER','SAMPLER')],['euler' if turbo else 'res_multistep'],{'sampler_name':'euler' if turbo else 'res_multistep'})
    add(9,'BasicScheduler','Schedule',[1550,490],[('model','MODEL')],[('SIGMAS','SIGMAS')],['simple',8 if turbo else 20,1.0],{'scheduler':'simple','steps':8 if turbo else 20,'denoise':1.0})
    add(10,'BasicGuider','Guidance',[1550,760],[('model','MODEL'),('conditioning','CONDITIONING')],[('GUIDER','GUIDER')])
    add(11,'SamplerCustomAdvanced','Joint video + audio sampling',[1950,40],[('noise','NOISE'),('guider','GUIDER'),('sampler','SAMPLER'),('sigmas','SIGMAS'),('latent_image','LATENT')],[('output','LATENT'),('denoised_output','LATENT')])
    add(12,'VAEDecodeTiled','Tiled video decode',[2340,40],[('samples','LATENT'),('vae','VAE')],[('IMAGE','IMAGE')],[256,64,32,8],{'tile_size':256,'overlap':64,'temporal_size':32,'temporal_overlap':8})
    add(13,'VAEDecodeAudio','Native stereo audio decode',[2340,340],[('samples','LATENT'),('vae','VAE')],[('AUDIO','AUDIO')])
    add(14,'CreateVideo','24 fps · sRGB',[2750,40],[('images','IMAGE'),('fps','FLOAT'),('audio','AUDIO')],[('VIDEO','VIDEO')],[24,8,'sRGB','none'],{'fps':24,'bit_depth':8,'color_space':'sRGB','codec':'none'})
    add(15,'SaveVideo','Export Promax MP4',[3150,40],[('video','VIDEO')],[('video','VIDEO')],['video/Promax','auto','auto','auto'],{'filename_prefix':'video/Promax','format':'auto','codec':'auto'})
    add(16,'MiniMaxH3PromaxLastFrame','Last frame for next FL2V clip',[2750,480],[('images','IMAGE')],[('image','IMAGE')])
    add(17,'SaveImage','Save last frame',[3150,480],[('images','IMAGE')],[],['Promax/last_frame'],{'filename_prefix':'Promax/last_frame'})
    model_sources={1:1,2:2}; clip_source=3
    if turbo:
        for id,src,file,y in [(18,1,'minimax_h3_fl2v_turbo_8step_v1.0_comfyui_bf16.safetensors',40),
                              (19,2,'minimax_h3_ref2v_turbo_8step_v1.0_768p_comfyui_bf16.safetensors',310)]:
            add(id,'LoraLoaderModelOnly','Matching Turbo LoRA',[380,y],[('model','MODEL')],[('MODEL','MODEL')],[file,1.0],{'lora_name':file,'strength_model':1.0})
            connect(src,0,id,'model'); model_sources[src]=id
        proj='mmh3-4b-ClipProj-v3.1.safetensors'
        add(20,'ClipProjApply','4B projection — external node required',[380,600],[('clip','CLIP')],[('CLIP','CLIP')],[proj],{'projection':proj})
        clip_source=20;connect(3,0,20,'clip')
    for src,name in [(model_sources[1],'model_fl2va'),(model_sources[2],'model_ref2va'),(clip_source,'clip'),(4,'vae'),(5,'audio_vae')]:connect(src,0,6,name)
    for args in [(6,0,9,'model'),(6,0,10,'model'),(6,1,10,'conditioning'),(7,0,11,'noise'),(10,0,11,'guider'),(8,0,11,'sampler'),(9,0,11,'sigmas'),(6,2,11,'latent_image'),(11,0,12,'samples'),(4,0,12,'vae'),(11,0,13,'samples'),(5,0,13,'vae'),(12,0,14,'images'),(6,3,14,'fps'),(13,0,14,'audio'),(14,0,15,'video'),(12,0,16,'images'),(16,0,17,'images')]: connect(*args)
    note=('PROMAX 0.1 — cible 12 Go VRAM / 48 Go RAM, non benchmarké sur GPU.\n'
          '1. Sélectionner les fichiers réellement installés dans chaque chargeur.\n'
          '2. T2V : FL2VA. FL2V : brancher Load Image sur first_frame. Ref2V : REF2VA, brancher reference_1 et renseigner sa description.\n'
          '3. Régler les plans dans Promax. Départ : 5 s, 480×864. 576×1024 donne un 9:16 exact.\n'
          '4. Vidéo + audio natif exportés. Dernière image sauvée pour le clip suivant.\n'
          '5. La reprise par image n’est pas une continuation latente. Pas de SageAttention requis.\n'
          + ('Variante expérimentale : ClipProjApply externe + projection 4B obligatoire. Turbo 8 steps peut réduire la qualité.' if turbo else 'Base H3 32B : charge importante en RAM. ComfyUI gère le déchargement GPU. Cette variante ne garantit pas de tenir dans 48 Go RAM.'))
    add(30,'MarkdownNote','À lire avant le premier rendu',[750,1180],[],[],[note]);nodes[-1]['size']=[680,340];api.pop('30')
    return {'last_node_id':30,'last_link_id':len(links),'nodes':nodes,'links':links,'groups':[], 'config':{},'extra':{'ds':{'scale':0.5,'offset':[40,40]}},'version':0.4},api

if __name__=='__main__':
    for turbo,name in [(False,'Promax-12GB-Base'),(True,'Promax-12GB-4B-Turbo')]:
        workflow,api=build(turbo)
        (ROOT/'example_workflows'/f'{name}.json').write_text(json.dumps(workflow,indent=2,ensure_ascii=False)+'\n')
        # The 4B external projection widget name must be checked against its actual schema
        # before shipping an API prompt; UI workflow inherits the attached user's node.
        if not turbo:
            (ROOT/'example_workflows'/f'{name}.api.json').write_text(json.dumps(api,indent=2,ensure_ascii=False)+'\n')
