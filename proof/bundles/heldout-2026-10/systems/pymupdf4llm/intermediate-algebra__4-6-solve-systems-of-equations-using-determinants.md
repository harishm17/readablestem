# **4.6 Solve Systems of Equations Using Determinants** 

## **Learning Objectives** 

By the end of this section, you will be able to: 

Evaluate the determinant of a 2 × 2 matrix Evaluate the determinant of a 3 × 3 matrix 

Use Cramer’s Rule to solve systems of equations Solve applications using determinants 

## **Be Prepared 4.6** 

Before you get started, take this readiness quiz. 

1. Simplify: 5(−2) −(−4)(1) . 

If you missed this problem, review Example 1.20. 

2. Simplify: −3(8 −10) + (−2)(6 −3) −4(−3 −(−4)) . If you missed this problem, review Example 1.19. 

3. Simplify: −−812<sup>.</sup> 

If you missed this problem, review Example 1.18. 

In this section we will learn of another method to solve systems of linear equations called Cramer’s rule. Before we can begin to use the rule, we need to learn some new definitions and notation. 

## **Evaluate the Determinant of a** 2 × 2 **Matrix** 

If a matrix has the same number of rows and columns, we call it a square matrix. Each square matrix has a real number associated with it called its determinant. To find the determinant of the square matrix [<sup>𝑎</sup> 𝑏 𝑏 𝑐 𝑑<sup>],we first write it as |𝑎</sup> 𝑐 𝑑<sup>| .To get the real number</sup> 

value of the determinate we subtract the products of the diagonals, as shown. 



## **Determinant** 

The determinant of any square matrix [<sup>𝑎</sup> 𝑏 𝑐 𝑑<sup>],where</sup><sup>_a, b, c,_and</sup><sup>_d_are real numbers, is</sup> |<sup>𝑎</sup> 𝑏 𝑐 𝑑<sup>| = 𝑎𝑑−𝑏𝑐</sup> 

Access for free at openstax.org. Intermediate Algebra (OpenStax), CC BY 4.0. Page 1 of 27 



<!-- Start of picture text -->
Example 4.45<br>Evaluate the determinate of  ⓐ [ 4 3 −2−1 ] ⓑ [ −3 −2 −40 ] .<br>Solution<br>ⓐ<br>Write the determinant.<br>Subtract the products of the diagonals.<br>Simplify.<br>Simplify.<br>ⓑ<br>Write the determinant.<br>Subtract the products of the diagonals.<br>Simplify.<br>Simplify.<br><!-- End of picture text -->



<!-- Start of picture text -->
Try It 4.89<br>Evaluate the determinate of  ⓐ [ 5 −3 −6<br>2 −4 ] ⓑ [ −4 0 7 ] .<br><!-- End of picture text -->

Access for free at openstax.org. Intermediate Algebra (OpenStax), CC BY 4.0. Page 2 of 27 

## **Try It 4.90** 



## **Evaluate the Determinant of a** 3 × 3 **Matrix** 

To evaluate the determinant of a 3 × 3 matrix, we have to be able to evaluate the minor of an entry in the determinant. The minor of an entry is the 2 × 2 determinant found by eliminating the row and column in the 3 × 3 determinant that contains the entry. 

## **Minor of an entry in** 3 × 3 **a Determinant** 

The **minor of an entry** in a 3 × 3 determinant is the 2 × 2 determinant found by eliminating the row and column in the 3 × 3 determinant that contains the entry. 

To find the minor of entry 𝑎1 , we eliminate the row and column which contain it. So we eliminate the first row and first column. Then we write the 2 × 2 determinant that remains. 



To find the minor of entry 𝑏2 , we eliminate the row and column that contain it. So we eliminate the 2<sup>nd</sup> row and 2<sup>nd</sup> column. Then we write the 2 × 2 determinant that remains. 



Access for free at openstax.org. Intermediate Algebra (OpenStax), CC BY 4.0. Page 3 of 27 

## **Example 4.46** 

|For the determinant||4<br>1<br>−2|−2<br>0<br>−4|3<br>−3<br>2<br>||,find and then evaluate the minor ofⓐ 𝑎1 ⓑ 𝑏3 ⓒ 𝑐2 .|
|---|---|---|---|---|



Access for free at openstax.org. Intermediate Algebra (OpenStax), CC BY 4.0. Page 4 of 27 

|**Solution**<br>ⓐ|
|---|
|Eliminate the row and column that contains𝑎1 .|
|Write the2 × 2determinant that remains.|
|Evaluate.|
|Simplify.|
|ⓑ|
|Eliminate the row and column that contains𝑏3 .|
|Write the2 × 2determinant that remains.|
|Evaluate.|
|Simplify.|
|ⓒ|
|Eliminate the row and column that contains𝑐2 .|
|Write the2 × 2determinant that remains.<br>Evaluate.|





























Access for free at openstax.org. Intermediate Algebra (OpenStax), CC BY 4.0. Page 5 of 27 

Simplify. 





<!-- Start of picture text -->
Try It 4.91<br>1 −1 4<br>For the determinant | 0 2 −1 |,  find and then evaluate the minor of  ⓐ 𝑎1 ⓑ 𝑏2 ⓒ 𝑐3 .<br>−2 −3 3<br>Try It 4.92<br>−2 −1 0<br>For the determinant | 3 0 −1 |,  find and then evaluate the minor of  ⓐ 𝑎2 ⓑ 𝑏3 ⓒ 𝑐2 .<br>−1 −2 3<br><!-- End of picture text -->

We are now ready to evaluate a 3 × 3 determinant. To do this we expand by minors, which allows us to evaluate the 3 × 3 determinant using 2 × 2 determinants—which we already know how to evaluate! 

To evaluate a 3 × 3 determinant by expanding by minors along the first row, we use the following pattern: 



Remember, to find the minor of an entry we eliminate the row and column that contains the entry. 

## **Expanding by Minors along the First Row to Evaluate a** 3 × 3 **Determinant** 

To evaluate a 3 × 3 determinant by **expanding by minors along the first row** , the following pattern: 



Access for free at openstax.org. Intermediate Algebra (OpenStax), CC BY 4.0. Page 6 of 27 



<!-- Start of picture text -->
Example 4.47<br>2 −3 −1<br>Evaluate the determinant | 3 2 0 | by expanding by minors along the first row.<br>−1 −1 −2<br>Solution<br>Expand by minors along the first row<br>Evaluate each determinant.<br>Simplify.<br>Simplify.<br>Simplify.<br>Try It 4.93<br>3 −2 4<br>Evaluate the determinant | 0 −1 −2 |,  by expanding by minors along the first row.<br>2 3 −1<br>Try It 4.94<br>3 −2 −2<br>Evaluate the determinant | 2 −1 4 |,  by expanding by minors along the first row.<br>−1 0 −3<br><!-- End of picture text -->

To evaluate a 3 × 3 determinant we can expand by minors using any row or column. Choosing a row or column other than the first row sometimes makes the work easier. 

When we expand by any row or column, we must be careful about the sign of the terms in the expansion. To determine the sign of the terms, we use the following sign pattern chart. 

Access for free at openstax.org. Intermediate Algebra (OpenStax), CC BY 4.0. Page 7 of 27 

+ − + | − + − | + − + 

## **Sign Pattern** 

When expanding by minors using a row or column, the sign of the terms in the expansion follow the following pattern. 

+ − + | − + − | + − + 

Notice that the sign pattern in the first row matches the signs between the terms in the expansion by the first row. 



Since we can expand by any row or column, how do we decide which row or column to use? Usually we try to pick a row or column that will make our calculation easier. If the determinant contains a 0, using the row or column that contains the 0 will make the calculations easier. 

Access for free at openstax.org. Intermediate Algebra (OpenStax), CC BY 4.0. Page 8 of 27 

## **Example 4.48** 



<!-- Start of picture text -->
4 −1 −3<br>Evaluate the determinant | 3 0 2 | by expanding by minors.<br>5 −4 −3<br><!-- End of picture text -->

### **Solution** 

To expand by minors, we look for a row or column that will make our calculations easier. Since 0 is in the second row and second column, expanding by either of those is a good choice. Since the second row has fewer negatives than the second column, we will expand by the second row. 



|Expand using the second row.|
|---|
|Be careful of the signs.|
|Evaluate each determinant.|
|Simplify.|
|Simplify.|
|Add.|













## **Try It 4.95** 



<!-- Start of picture text -->
2 −1 −3<br>Evaluate the determinant | 0 3 −4 | by expanding by minors.<br>3 −4 −3<br><!-- End of picture text -->

## **Try It 4.96** 

|Evaluate the determinant||−2<br>−1|−1<br>2|−3<br>2<br>||by expanding by minors.|
|---|---|---|---|---|
||4|−4|0||



Access for free at openstax.org. Intermediate Algebra (OpenStax), CC BY 4.0. Page 9 of 27 

## **Use Cramer’s Rule to Solve Systems of Equations** 

Cramer’s Rule is a method of solving systems of equations using determinants. It can be derived by solving the general form of the systems of equations by elimination. Here we will demonstrate the rule for both systems of two equations with two variables and for systems of three equations with three variables. 

Let’s start with the systems of two equations with two variables. 

## **Cramer’s Rule for Solving a System of Two Equations** 



<!-- Start of picture text -->
For the system of equations { 𝑎1 𝑥+ 𝑏1 𝑦= 𝑘1 ,  the solution (𝑥, 𝑦) can be determined by<br>𝑎2 𝑥+ 𝑏2 𝑦= 𝑘2<br><!-- End of picture text -->



Notice that to form the determinant _D_ , we use take the coefficients of the variables. 



Notice that to form the determinant 𝐷𝑥 and 𝐷𝑦 , we substitute the constants for the coefficients of the variable we are finding. 



Access for free at openstax.org. Intermediate Algebra (OpenStax), CC BY 4.0. Page 10 of 27 



<!-- Start of picture text -->
Example 4.49<br>How to Solve a System of Equations Using Cramer’s Rule<br>Solve using Cramer’s Rule: { 2𝑥+ 𝑦= −4<br>3𝑥−2𝑦= −6 .<br>Solution<br><!-- End of picture text -->

Access for free at openstax.org. Intermediate Algebra (OpenStax), CC BY 4.0. Page 11 of 27 

## **Try It 4.97** 

Solve using Cramer’s rule: {<sup>3𝑥+ 𝑦= −3</sup> 2𝑥+ 3𝑦= 6<sup>.</sup> 

## **Try It 4.98** 

Solve using Cramer’s rule: {<sup>−𝑥+ 𝑦= 2</sup> 2𝑥+ 𝑦= −4<sup>.</sup> 

## **How To** 

### **Solve a system of two equations using Cramer’s rule.** 

1. Step 1. Evaluate the determinant _D_ , using the coefficients of the variables. 

2. Step 2. Evaluate the determinant 𝐷𝑥 . Use the constants in place of the _x_ coefficients. 

3. Step 3. Evaluate the determinant 𝐷𝑦 . Use the constants in place of the _y_ coefficients. 

- 𝐷𝑥 𝐷𝑦 

- 4. Step 4. Find _x_ and _y_ . 𝑥= 𝐷<sup>,𝑦=</sup> 𝐷 

5. Step 5. Write the solution as an ordered pair. 

6. Step 6. Check that the ordered pair is a solution to both original equations. 

To solve a system of three equations with three variables with Cramer’s Rule, we basically do what we did for a system of two equations. However, we now have to solve for three variables to get the solution. The determinants are also going to be 3 × 3 which will make our work more interesting! 

Access for free at openstax.org. Intermediate Algebra (OpenStax), CC BY 4.0. Page 12 of 27 

|**Cramer’s Rule for Solving a System of Three Equations**<br>𝑎1𝑥+ 𝑏1𝑦+ 𝑐1𝑧= 𝑘1|
|---|
|For the system of equations{<br>𝑎2𝑥+ 𝑏2𝑦+ 𝑐2𝑧= 𝑘2<br>,the solution(𝑥, 𝑦, 𝑧)can be determined by|
|𝑎3𝑥+ 𝑏3𝑦+ 𝑐3𝑧= 𝑘3|





Access for free at openstax.org. Intermediate Algebra (OpenStax), CC BY 4.0. Page 13 of 27 

|**Example 4.50**|
|---|
|3𝑥−5𝑦+ 4𝑧= 5|
|Solve the system of equations using Cramer’s Rule:{<br>5𝑥+ 2𝑦+ 𝑧= 0<br>.|
|2𝑥+ 3𝑦−2𝑧= 3|



Access for free at openstax.org. Intermediate Algebra (OpenStax), CC BY 4.0. Page 14 of 27 

|**Solution**|
|---|
|Evaluate the determinant_D_.|
|Expand by minors using column 1.|
|Evaluate the determinants.|
|Simplify.|
|Simplify.|
|Simplify.|
|Evaluate the determinant𝐷𝑥.Use the<br>constants to replace the coefficients of_x_.|
|Expand by minors using column 1.|
|Evaluate the determinants.|
|Simplify.|
|Simplify.|
|Evaluate the determinant𝐷𝑦.Use the<br>constants to replace the coefficients of_y_.|
|Evaluate the determinants.|
|Simplify.|
|Simplify.|
|Simplify.|
|Evaluate the determinant𝐷𝑧.Use the|
|constants to replace the coefficients of_z_.|







































Access for free at openstax.org. Intermediate Algebra (OpenStax), CC BY 4.0. Page 15 of 27 





|Evaluate the determinants.||
|---|---|
|Simplify.||
|Simplify.||
|Simplify.||
|Find_x_,_y_, and_z_.||
|Substitute in the values.||
|Simplify.||
|Write the solution as an ordered triple.||
|Check that the ordered triple is a solution<br>to**all three**original equations.|We leave the check to you.|
||The solution is(2, −3, −4) .|



|**Try It 4.99**|
|---|
|Solve the system of equations using Cramer’s Rule:{<br>3𝑥+ 8𝑦+ 2𝑧= −5<br>2𝑥+ 5𝑦−3𝑧= 0<br>𝑥+ 2𝑦−2𝑧= −1<br>.|
|**Try It 4.100**|
|Solve the system of equations using Cramer’s Rule:{<br>3𝑥+ 𝑦−6𝑧= −3<br>2𝑥+ 6𝑦+ 3𝑧= 0<br>3𝑥+ 2𝑦−3𝑧= −6<br>.|



Cramer’s rule does not work when the value of the _D_ determinant is 0, as this would mean we would be dividing by 0. But when 𝐷= 0, the system is either inconsistent or dependent. 

When the value of 𝐷= 0 and 𝐷𝑥 , 𝐷𝑦 and 𝐷𝑧 are all zero, the system is consistent and dependent and there are infinitely many solutions. 

When the value of 𝐷= 0 and 𝐷𝑥 , 𝐷𝑦 and 𝐷𝑧 are not all zero, the system is inconsistent and there is no solution. 

Access for free at openstax.org. Intermediate Algebra (OpenStax), CC BY 4.0. Page 16 of 27 

## **Dependent and Inconsistent Systems of Equations** 

For any system of equations, where the **value of the determinant** 𝐷= 0, 

𝐕𝐚𝐥𝐮𝐞 𝐨𝐟 𝐝𝐞𝐭𝐞𝐫𝐦𝐢𝐧𝐚𝐧𝐭𝐬 𝐓𝐲𝐩𝐞 𝐨𝐟 𝐬𝐲𝐬𝐭𝐞𝐦 𝐒𝐨𝐥𝐮𝐭𝐢𝐨𝐧 𝐷= 0 and 𝐷𝑥 , 𝐷𝑦 and 𝐷𝑧 are all zero consistent and dependent infinitely many solutions 𝐷= 0 and 𝐷𝑥 , 𝐷𝑦 and 𝐷𝑧 are not all zero inconsistent no solution 

In the next example, we will use the values of the determinants to find the solution of the system. 

## **Example 4.51** 

𝑥+ 3𝑦= 4 Solve the system of equations using Cramer’s rule : { −2𝑥−6𝑦= 3<sup>.</sup> **Solution** 𝑥+ 3𝑦= 4 { −2𝑥−6𝑦= 3 Evaluate the determinant 𝐷, using the 𝐷= |<sup>1</sup> 3 coefficients of the variables. −2 −6<sup>|</sup> 𝐷= −6 −(−6) 𝐷= 0 

We cannot use Cramer’s Rule to solve this system. But by looking at the value of the determinants 𝐷𝑥 and 𝐷𝑦 , we can determine whether the system is dependent or inconsistent. 3 Evaluate the determinant 𝐷𝑥 . 𝐷𝑥 = |<sup>4</sup> 3 −6<sup>|</sup> 𝐷𝑥 = −24 −9 𝐷𝑥 = 15 

Since all the determinants are not zero, the system is inconsistent. There is no solution. 

## **Try It 4.101** 

Solve the system of equations using Cramer’s rule: {<sup>4𝑥−3𝑦= 8</sup> 8𝑥−6𝑦= 14<sup>.</sup> 

Access for free at openstax.org. Intermediate Algebra (OpenStax), CC BY 4.0. Page 17 of 27 

## **Try It 4.102** 

Solve the system of equations using Cramer’s rule: {<sup>𝑥= −3𝑦+ 4</sup> 2𝑥+ 6𝑦= 8<sup>.</sup> 

## **Solve Applications using Determinants** 

An interesting application of determinants allows us to test if points are collinear. Three points (𝑥1 , 𝑦1 ), (𝑥2 , 𝑦2 ) and (𝑥3 , 𝑦3 ) are collinear if and only if the determinant below is zero. 



## **Test for Collinear Points** 

Three points (𝑥1 , 𝑦1 ), (𝑥2 , 𝑦2 ) and (𝑥3 , 𝑦3 ) are collinear if and only if 



We will use this property in the next example. 

Access for free at openstax.org. Intermediate Algebra (OpenStax), CC BY 4.0. Page 18 of 27 

## **Example 4.52** 

Determine whether the points (5, −5), (4, −3), and (3, −1) are collinear. 



<!-- Start of picture text -->
Solution<br>Substitute the values into the determinant.<br>(5, −5), (4, −3),  and (3, −1)<br>Evaluate the determinant by expanding<br>by minors using column 3.<br>Evaluate the determinants.<br>Simplify.<br>Simplify.<br>The value of the determinate is 0, so the<br>points are collinear.<br><!-- End of picture text -->

## **Try It 4.103** 

Determine whether the points (3, −2), (5, −3), and (1, −1) are collinear. 

## **Try It 4.104** 

Determine whether the points (−4, −1), (−6, 2), and (−2, −4) are collinear. 

Access for free at openstax.org. Intermediate Algebra (OpenStax), CC BY 4.0. Page 19 of 27 

## **Media** 

Access these online resources for additional instruction and practice with solving systems of linear inequalities by graphing. 

Solving Systems of Linear Inequalities by Graphing 

Systems of Linear Inequalities 

## **Section 4.6 Exercises** 

### **Practice Makes Perfect** 

#### **Evaluate the Determinant of a 2 × 2 Matrix** 

In the following exercises, evaluate the determinate of each square matrix. 



<!-- Start of picture text -->
232.<br>[ 6 −2<br>3 −1 ]<br>233.<br>[ −4 8<br>−3 5 ]<br>234.<br>[ −3 5<br>0 −4 ]<br>235.<br>[ −2 0<br>7 −5 ]<br><!-- End of picture text -->

#### **Evaluate the Determinant of a 3 × 3 Matrix** 

In the following exercises, find and then evaluate the indicated minors. 



<!-- Start of picture text -->
236.<br>3 −1 4<br>| −1 0 −2 |<br>−4 1 5<br>Find the minor  ⓐ 𝑎1 ⓑ 𝑏2 ⓒ 𝑐3<br><!-- End of picture text -->

Access for free at openstax.org. Intermediate Algebra (OpenStax), CC BY 4.0. Page 20 of 27 



<!-- Start of picture text -->
237.<br>−1 −3 2<br>| 4 −2 −1 |<br>−2 0 −3<br>Find the minor  ⓐ 𝑎1 ⓑ 𝑏1 ⓒ 𝑐2<br>238.<br>2 −3 −4<br>| −1 2 −3 |<br>0 −1 −2<br>Find the minor  ⓐ 𝑎2 ⓑ 𝑏2 ⓒ 𝑐2<br>239.<br>−2 −2 3<br>| 1 −3 0 |<br>−2 3 −2<br>Find the minor  ⓐ 𝑎3 ⓑ 𝑏3 ⓒ 𝑐3<br><!-- End of picture text -->

In the following exercises, evaluate each determinant by expanding by minors along the first row. 



<!-- Start of picture text -->
240.<br>−2 3 −1<br>| −1 2 −2 |<br>3 1 −3<br>241.<br>4 −1 −2<br>| −3 −2 1 |<br>−2 −5 7<br>242.<br>−2 −3 −4<br>| 5 −6 7 |<br>−1 2 0<br>243.<br>1 3 −2<br>| 5 −6 4 |<br>0 −2 −1<br><!-- End of picture text -->

Access for free at openstax.org. Intermediate Algebra (OpenStax), CC BY 4.0. Page 21 of 27 

In the following exercises, evaluate each determinant by expanding by minors. 



<!-- Start of picture text -->
244.<br>−5 −1 −4<br>| 4 0 −3 |<br>2 −2 6<br>245.<br>4 −1 3<br>| 3 −2 2 |<br>−1 0 4<br>246.<br>3 5 4<br>| −1 3 0 |<br>−2 6 1<br>247.<br>2 −4 −3<br>| 5 −1 −4 |<br>3 2 0<br><!-- End of picture text -->

#### **Use Cramer’s Rule to Solve Systems of Equations** 

In the following exercises, solve each system of equations using Cramer’s Rule. 



<!-- Start of picture text -->
248.<br>{ −2𝑥+ 3𝑦= 3<br>𝑥+ 3𝑦= 12<br>249.<br>{ 𝑥−2𝑦= −5<br>2𝑥−3𝑦= −4<br>250.<br>{ 𝑥−3𝑦= −9<br>2𝑥+ 5𝑦= 4<br><!-- End of picture text -->

Access for free at openstax.org. Intermediate Algebra (OpenStax), CC BY 4.0. Page 22 of 27 



<!-- Start of picture text -->
251.<br>{ 2𝑥+ 𝑦= −4<br>3𝑥−2𝑦= −6<br>252.<br>{ 𝑥−2𝑦= −5<br>2𝑥−3𝑦= −4<br>253.<br>{ 𝑥−3𝑦= −9<br>2𝑥+ 5𝑦= 4<br>254.<br>{ 5𝑥−3𝑦= −1<br>2𝑥−𝑦= 2<br>255.<br>{ 3𝑥+ 8𝑦= −3<br>2𝑥+ 5𝑦= −3<br>256.<br>6𝑥−5𝑦+ 2𝑧= 3<br>{ 2𝑥+ 𝑦−4𝑧= 5<br>3𝑥−3𝑦+ 𝑧= −1<br>257.<br>4𝑥−3𝑦+ 𝑧= 7<br>{ 2𝑥−5𝑦−4𝑧= 3<br>3𝑥−2𝑦−2𝑧= −7<br><!-- End of picture text -->

Access for free at openstax.org. Intermediate Algebra (OpenStax), CC BY 4.0. Page 23 of 27 

258. 2𝑥−5𝑦+ 3𝑧= 8 { 3𝑥−𝑦+ 4𝑧= 7 𝑥+ 3𝑦+ 2𝑧= −3 259. 11𝑥+ 9𝑦+ 2𝑧= −9 { 7𝑥+ 5𝑦+ 3𝑧= −7 4𝑥+ 3𝑦+ 𝑧= −3 260. 𝑥+ 2𝑧= 0 { 4𝑦+ 3𝑧= −2 2𝑥−5𝑦= 3 261. 2𝑥+ 5𝑦= 4 { 3𝑦−𝑧= 3 4𝑥+ 3𝑧= −3 262. 2𝑦+ 3𝑧= −1 { 5𝑥+ 3𝑦= −6 7𝑥+ 𝑧= 1 263. 3𝑥−𝑧= −3 { 5𝑦+ 2𝑧= −6 4𝑥+ 3𝑦= −8 264. {<sup>2𝑥+ 𝑦= 3</sup> 6𝑥+ 3𝑦= 9 

Access for free at openstax.org. Intermediate Algebra (OpenStax), CC BY 4.0. Page 24 of 27 



<!-- Start of picture text -->
265.<br>𝑥−4𝑦= −1<br>{<br>−3𝑥+ 12𝑦= 3<br>266.<br>−3𝑥−𝑦= 4<br>{<br>6𝑥+ 2𝑦= −16<br>267.<br>4𝑥+ 3𝑦= 2<br>{<br>20𝑥+ 15𝑦= 5<br>268.<br>𝑥+ 𝑦−3𝑧= −1<br>{ 𝑦−𝑧= 0<br>−𝑥+ 2𝑦= 1<br>269.<br>2𝑥+ 3𝑦+ 𝑧= 12<br>{ 𝑥+ 𝑦+ 𝑧= 9<br>3𝑥+ 4𝑦+ 2𝑧= 20<br>270.<br>3𝑥+ 4𝑦−3𝑧= −2<br>{ 2𝑥+ 3𝑦−𝑧= −12<br>𝑥+ 𝑦−2𝑧= 6<br>271.<br>𝑥−2𝑦+ 3𝑧= 1<br>{ 𝑥+ 𝑦−3𝑧= 7<br>3𝑥−4𝑦+ 5𝑧= 7<br><!-- End of picture text -->

#### **Solve Applications Using Determinants** 

In the following exercises, determine whether the given points are collinear. 

Access for free at openstax.org. Intermediate Algebra (OpenStax), CC BY 4.0. Page 25 of 27 

272. (0, 1), (2, 0), and (−2, 2) . 273. (0, −5), (−2, −2), and (2, −8) . 274. (4, −3), (6, −4), and (2, −2) . 275. (−2, 1), (−4, 4), and (0, −2) . 

### **Writing Exercises** 

276. Explain the difference between a square matrix and its determinant. Give an example of each. 277. Explain what is meant by the minor of an entry in a square matrix. 278. Explain how to decide which row or column you will use to expand a 3 × 3 determinant. 279. Explain the steps for solving a system of equations using Cramer’s rule. 

### **Self Check** 

- ⓐ After completing the exercises, use this checklist to evaluate your mastery of the objectives of this section. 

Access for free at openstax.org. Intermediate Algebra (OpenStax), CC BY 4.0. Page 26 of 27 



ⓑ After reviewing this checklist, what will you do to become confident for all objectives? 

“4.6 Solve Systems of Equations Using Determinants” from Intermediate Algebra by Lynn Marecek, OpenStax. 

Licensed under Creative Commons Attribution License (CC BY 4.0). http://creativecommons.org/licenses/by/4.0/ 

Original page: <u>https://openstax.org/books/intermediate-algebra/pages/4-6-solve-systems-of-equations-using-determinants</u> Access for free at openstax.org. 

Changes: reformatted as a printable page for evaluating ReadableSTEM; image alt text left out of this print; no wording edited. 

ReadableSTEM is not affiliated with or endorsed by OpenStax. 

Access for free at openstax.org. Intermediate Algebra (OpenStax), CC BY 4.0. Page 27 of 27 

