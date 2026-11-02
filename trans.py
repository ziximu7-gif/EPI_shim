import SimpleITK as sitk

# ref_img: 被试外/phantom 或 “参考空间”的结构图（或同几何的参考）
# mov_img: 被试内结构像（3D GRE/MP2RAGE）
ref_img = sitk.ReadImage("ref_struct.nii.gz", sitk.sitkFloat32)
mov_img = sitk.ReadImage("subj_struct.nii.gz", sitk.sitkFloat32)

# 初始化：用质心对齐 + 刚体变换
initial = sitk.CenteredTransformInitializer(
    ref_img, mov_img, sitk.Euler3DTransform(),
    sitk.CenteredTransformInitializerFilter.GEOMETRY
)

reg = sitk.ImageRegistrationMethod()
reg.SetMetricAsMattesMutualInformation(numberOfHistogramBins=50)
reg.SetMetricSamplingStrategy(reg.RANDOM)
reg.SetMetricSamplingPercentage(0.2)
reg.SetInterpolator(sitk.sitkLinear)

reg.SetOptimizerAsGradientDescent(
    learningRate=1.0, numberOfIterations=200,
    convergenceMinimumValue=1e-6, convergenceWindowSize=10
)
reg.SetOptimizerScalesFromPhysicalShift()

reg.SetInitialTransform(initial, inPlace=False)

# 只做刚体：Euler3DTransform
final_transform = reg.Execute(ref_img, mov_img)

# 把参考基函数场（3D volume）重采样到被试空间
basis_ref = sitk.ReadImage("basis_k_ref.nii.gz", sitk.sitkFloat32)

resampled = sitk.Resample(
    basis_ref,                  # moving image
    mov_img,                    # reference geometry = subj space
    final_transform,            # map ref->subj (注意方向，必要时取逆)
    sitk.sitkLinear,
    0.0,
    basis_ref.GetPixelID()
)

sitk.WriteImage(resampled, "basis_k_in_subj_space.nii.gz")
