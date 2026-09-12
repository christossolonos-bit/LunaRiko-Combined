import * as THREE from 'three';
import { GLTFLoader }   from 'three/addons/loaders/GLTFLoader.js';
import { VRMLoaderPlugin, VRMUtils } from '@pixiv/three-vrm';
import { createVRMAnimationClip, VRMAnimationLoaderPlugin, VRMLookAtQuaternionProxy } from '@pixiv/three-vrm-animation';

export async function loadVRM(path, scene) {
  const loader = new GLTFLoader();
  loader.crossOrigin = 'anonymous';
  loader.register(parser => new VRMLoaderPlugin(parser));
  loader.register(parser => new VRMAnimationLoaderPlugin(parser));


  const gltf = await loader.loadAsync(path);
  const vrm  = gltf.userData.vrm;
  // calling these functions greatly improves the performance
  		VRMUtils.removeUnnecessaryVertices( gltf.scene );
			VRMUtils.combineSkeletons( gltf.scene );
			VRMUtils.combineMorphs( vrm );
 

    vrm.scene.traverse( ( obj ) => {obj.frustumCulled = false;
    obj.castShadow = true; // 👈 Add this to cast shadows
  } );

  const lookAtQuatProxy = new VRMLookAtQuaternionProxy( vrm.lookAt );
  lookAtQuatProxy.name = 'lookAtQuaternionProxy';
  vrm.scene.add( lookAtQuatProxy );
  scene.add(vrm.scene);
  return {vrm, loader};
}
